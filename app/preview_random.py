"""Session-local replay of reviewed consequences; never draw again at confirmation."""
import copy
import random


class PreviewChanged(ValueError):
    def __init__(self):
        super().__init__('The roll or its consequences changed. Review an updated preview; nothing was applied.')


class RandomTape:
    def __init__(self, entries=None):
        self.replaying = entries is not None
        self.entries = copy.deepcopy(entries or [])
        self.position = 0

    def draw(self, method, arguments, generate):
        if self.replaying:
            if self.position >= len(self.entries):
                raise PreviewChanged()
            entry = self.entries[self.position]
            self.position += 1
            if entry['method'] != method or entry['arguments'] != arguments:
                raise PreviewChanged()
            return entry['value']
        value = generate()
        self.entries.append({'method': method, 'arguments': arguments, 'value': value})
        return value

    def randint(self, low, high):
        return self.draw('randint', [low, high], lambda: random.SystemRandom().randint(low, high))

    def choice(self, values):
        # Persist a selected Sim's identity, not its position in a changing query.
        def identity(value):
            return {'record_id': value.id} if hasattr(value, 'id') else value
        identities = [identity(value) for value in values]
        chosen = self.draw('choice', identities, lambda: identity(random.SystemRandom().choice(values)))
        return values[identities.index(chosen)]

    def finish(self):
        if self.replaying and self.position != len(self.entries):
            raise PreviewChanged()


def roll_rng(session):
    return session.info.get('preview_random') or random.SystemRandom()
