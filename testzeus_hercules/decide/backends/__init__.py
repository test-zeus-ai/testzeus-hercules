"""Decision backends.

``local_llm`` asks the local chat model (pure mode); ``jev`` asks JEV, a hosted
decision model, and ``jev_fallback`` wraps it for hybrid mode (the local model
answers when JEV cannot); ``clef`` asks a local Clef server through the same client
(pure mode, wrapped the same way). ``stub`` returns fixed answers and is a test
double only: no run decides with coded rules. Backends that need a server are
imported from their own modules.
"""

from testzeus_hercules.decide.backends.stub import StubCall, StubDecider, default_answer

__all__ = ["StubCall", "StubDecider", "default_answer"]
