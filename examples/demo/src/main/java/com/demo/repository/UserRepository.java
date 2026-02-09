package com.demo.repository;

public class UserRepository {
    public String findById(Long id) {
        return "u-" + id;
    }
}
