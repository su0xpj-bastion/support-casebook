class TokenBucket:
    def __init__(self, rate, capacity, now=0.0):
        self.rate, self.capacity = rate, capacity
        self.tokens, self.last = capacity, now

    def allow(self, now):
        if now - self.last >= 1 / self.rate:
            self.tokens = 1
            self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
