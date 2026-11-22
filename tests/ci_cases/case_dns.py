import socket


def test_external_host():
    socket.getaddrinfo("casebook-ci-check.invalid", 443)
