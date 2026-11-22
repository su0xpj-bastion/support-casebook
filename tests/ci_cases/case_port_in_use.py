import socket


def test_fixed_port_collides():
    a = socket.socket()
    a.bind(("127.0.0.1", 0))
    a.listen()
    b = socket.socket()
    try:
        b.bind(("127.0.0.1", a.getsockname()[1]))   # the same fixed port, while the first is listening
    finally:
        a.close()
        b.close()
