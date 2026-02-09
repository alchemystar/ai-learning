package com.demo.service.impl;

import com.demo.api.UserService;
import com.demo.repository.UserRepository;

public class UserServiceImpl implements UserService {
    private UserRepository userRepository;

    public String getUserById(Long id) {
        String user = userRepository.findById(id);
        return user;
    }
}
