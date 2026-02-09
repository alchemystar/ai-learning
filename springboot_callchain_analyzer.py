#!/usr/bin/env python3
import argparse
import json
import os
import re
import textwrap
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


JAVA_FILE_RE = re.compile(r".*\.java$")
PACKAGE_RE = re.compile(r"\bpackage\s+([\w\.]+)\s*;")
IMPORT_RE = re.compile(r"\bimport\s+([\w\.\*]+)\s*;")
TYPE_RE = re.compile(r"\b(class|interface)\s+(\w+)([^\{]*)\{")
IMPLEMENTS_RE = re.compile(r"\bimplements\s+([^\{]+)")
EXTENDS_RE = re.compile(r"\bextends\s+([^\{]+)")
METHOD_SIG_RE = re.compile(
    r"(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?"
    r"[\w\<\>\[\]\.,\s\?]+\s+(\w+)\s*\(([^)]*)\)\s*\{"
)
INTERFACE_METHOD_RE = re.compile(
    r"(?:public\s+)?(?:default\s+)?[\w\<\>\[\]\.,\s\?]+\s+(\w+)\s*\(([^)]*)\)\s*;"
)
FIELD_RE = re.compile(r"(?:private|protected|public)?\s*([\w<>\[\]\.]+)\s+(\w+)\s*;")
OBJ_CALL_RE = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*\(")
DIRECT_CALL_RE = re.compile(r"\b(\w+)\s*\(")

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "throw", "super", "this", "try", "synchronized",
}


@dataclass
class MethodInfo:
    name: str
    params: str
    body: str


@dataclass
class TypeInfo:
    package: str
    name: str
    fqcn: str
    kind: str
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    fields: Dict[str, str] = field(default_factory=dict)
    methods: Dict[str, MethodInfo] = field(default_factory=dict)


@dataclass
class CallNode:
    signature: str
    children: List["CallNode"] = field(default_factory=list)


class LLMClient:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def suggest(self, caller_sig: str, unresolved: List[str], context: str) -> List[str]:
        if not self.enabled or not self.api_key:
            return []

        prompt = textwrap.dedent(
            f"""
            你是 Java/SpringBoot 调用链分析助手。
            给定调用方和无法静态解析的方法名，请给出最可能的被调用签名。
            输出 JSON 数组，每项为字符串，格式："com.demo.Class#method"。

            调用方: {caller_sig}
            unresolved: {unresolved}
            context:
            {context}
            """
        ).strip()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是严谨的静态分析辅助器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            start = content.find("[")
            end = content.rfind("]")
            if start >= 0 and end > start:
                return [s for s in json.loads(content[start : end + 1]) if isinstance(s, str)]
        except Exception:
            return []
        return []


class Analyzer:
    def __init__(self, project_dir: str, use_llm: bool = False, max_depth: int = 6):
        self.project_dir = project_dir
        self.types: Dict[str, TypeInfo] = {}
        self.simple_index: Dict[str, List[str]] = {}
        self.llm = LLMClient(enabled=use_llm)
        self.max_depth = max_depth

    def load(self) -> None:
        for root, _, files in os.walk(self.project_dir):
            for name in files:
                if not JAVA_FILE_RE.match(name):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    src = f.read()
                t = self._parse_type(src)
                if t:
                    self.types[t.fqcn] = t
                    self.simple_index.setdefault(t.name, []).append(t.fqcn)

    def _parse_type(self, src: str) -> Optional[TypeInfo]:
        pkg_m = PACKAGE_RE.search(src)
        if not pkg_m:
            return None
        package = pkg_m.group(1)

        imports = [m.group(1) for m in IMPORT_RE.finditer(src)]
        type_m = TYPE_RE.search(src)
        if not type_m:
            return None

        kind = type_m.group(1)
        name = type_m.group(2)
        rest = type_m.group(3)
        fqcn = f"{package}.{name}"

        impls, ext = [], []
        impl_m = IMPLEMENTS_RE.search(rest)
        if impl_m:
            impls = [s.strip().split("<")[0] for s in impl_m.group(1).split(",")]
        ext_m = EXTENDS_RE.search(rest)
        if ext_m:
            ext = [s.strip().split("<")[0] for s in ext_m.group(1).split(",")]

        fields: Dict[str, str] = {}
        for fm in FIELD_RE.finditer(src):
            tpe = fm.group(1).split("<")[0].split(".")[-1]
            fields[fm.group(2)] = tpe

        methods = self._extract_methods(src, kind)
        return TypeInfo(package, name, fqcn, kind, ext, impls, imports, fields, methods)

    def _extract_methods(self, src: str, kind: str) -> Dict[str, MethodInfo]:
        methods: Dict[str, MethodInfo] = {}
        for m in METHOD_SIG_RE.finditer(src):
            name = m.group(1)
            params = m.group(2)
            body_start = m.end() - 1
            body, _ = self._extract_block(src, body_start)
            if body is not None:
                methods[name] = MethodInfo(name, params, body)

        if kind == "interface":
            for m in INTERFACE_METHOD_RE.finditer(src):
                name = m.group(1)
                params = m.group(2)
                methods.setdefault(name, MethodInfo(name, params, ""))
        return methods

    def _extract_block(self, src: str, start_brace_idx: int) -> Tuple[Optional[str], int]:
        if start_brace_idx >= len(src) or src[start_brace_idx] != "{":
            return None, start_brace_idx
        depth = 0
        for i in range(start_brace_idx, len(src)):
            c = src[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return src[start_brace_idx + 1 : i], i
        return None, start_brace_idx

    def analyze_interface(self, interface_fqcn: str, method: Optional[str] = None) -> List[CallNode]:
        iface = self.types.get(interface_fqcn)
        if not iface or iface.kind != "interface":
            raise ValueError(f"接口不存在或不是 interface: {interface_fqcn}")

        targets = [method] if method else list(iface.methods.keys())
        impls = self._find_implementations(interface_fqcn, iface.name)

        roots: List[CallNode] = []
        for m in targets:
            root = CallNode(signature=f"{interface_fqcn}#{m}")
            for impl in impls:
                if m in impl.methods:
                    child = self._build_call_tree(impl, m, 1, set())
                    child.signature = f"{impl.fqcn}#{m}"
                    root.children.append(child)
            roots.append(root)
        return roots

    def _find_implementations(self, interface_fqcn: str, interface_simple: str) -> List[TypeInfo]:
        out = []
        for t in self.types.values():
            if t.kind == "class" and (interface_simple in t.implements or interface_fqcn in t.implements):
                out.append(t)
        return out

    def _build_call_tree(self, t: TypeInfo, method_name: str, depth: int, visiting: Set[str]) -> CallNode:
        sig = f"{t.fqcn}#{method_name}"
        node = CallNode(signature=sig)
        if depth > self.max_depth or sig in visiting:
            return node
        visiting.add(sig)

        method = t.methods.get(method_name)
        if not method:
            visiting.remove(sig)
            return node

        resolved, unresolved = self._resolve_invocations(t, method)
        for callee_fqcn, callee_method in sorted(resolved):
            callee_t = self.types.get(callee_fqcn)
            if not callee_t:
                node.children.append(CallNode(signature=f"{callee_fqcn}#{callee_method}"))
                continue
            child = self._build_call_tree(callee_t, callee_method, depth + 1, visiting)
            child.signature = f"{callee_fqcn}#{callee_method}"
            node.children.append(child)

        if unresolved:
            suggestions = self.llm.suggest(sig, sorted(unresolved), method.body[:2000])
            for s in suggestions:
                node.children.append(CallNode(signature=f"[LLM] {s}"))

        visiting.remove(sig)
        return node

    def _resolve_invocations(self, t: TypeInfo, method: MethodInfo) -> Tuple[Set[Tuple[str, str]], Set[str]]:
        resolved, unresolved = set(), set()

        for obj, m in OBJ_CALL_RE.findall(method.body):
            if obj in {"this", "super"}:
                (resolved if m in t.methods else unresolved).add((t.fqcn, m) if m in t.methods else m)
                continue
            field_type = t.fields.get(obj)
            if not field_type:
                unresolved.add(m)
                continue
            candidates = self.simple_index.get(field_type, [])
            hit = False
            for c in candidates:
                ct = self.types.get(c)
                if ct and m in ct.methods:
                    resolved.add((c, m))
                    hit = True
            if not hit:
                unresolved.add(m)

        for m in DIRECT_CALL_RE.findall(method.body):
            if m in KEYWORDS:
                continue
            if m in t.methods:
                resolved.add((t.fqcn, m))
            else:
                unresolved.add(m)

        return resolved, unresolved


def render_tree(node: CallNode, prefix: str = "", is_last: bool = True) -> List[str]:
    connector = "└── " if is_last else "├── "
    line = f"{prefix}{connector}{node.signature}" if prefix else node.signature
    lines = [line]
    child_prefix = prefix + ("    " if is_last else "│   ")
    for idx, child in enumerate(node.children):
        lines.extend(render_tree(child, child_prefix, idx == len(node.children) - 1))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="SpringBoot Java 接口调用链分析器（含 LLM 辅助）")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--interface", required=True, dest="interface_fqcn")
    parser.add_argument("--method")
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()

    analyzer = Analyzer(args.project_dir, use_llm=args.use_llm, max_depth=args.max_depth)
    analyzer.load()
    trees = analyzer.analyze_interface(args.interface_fqcn, args.method)
    for t in trees:
        print("\n".join(render_tree(t)))


if __name__ == "__main__":
    main()
