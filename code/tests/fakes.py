"""Synthetic credentials; no account or Keychain access."""


class MemoryVault:
    def __init__(self):
        self.values = {}

    def set(self, identity, value):
        self.values[identity] = value

    def get(self, identity):
        if identity not in self.values:
            raise ValueError("No synthetic credential.")
        return self.values[identity]

    def delete(self, identity):
        self.values.pop(identity, None)
