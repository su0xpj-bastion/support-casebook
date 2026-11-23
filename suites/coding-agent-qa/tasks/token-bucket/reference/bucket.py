class TokenBucket:
    def __init__(self, rate, capacity, now=0.0):
        self.rate, self.capacity = rate, capacity
        self.tokens, self.last = float(capacity), now

    def allow(self, now):
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
