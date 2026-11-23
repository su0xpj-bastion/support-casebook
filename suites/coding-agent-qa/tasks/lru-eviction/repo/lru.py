class LRU:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def put(self, key, value):
        self.data[key] = value
        if len(self.data) > self.capacity:
            last = list(self.data)[-1]
            del self.data[last]
