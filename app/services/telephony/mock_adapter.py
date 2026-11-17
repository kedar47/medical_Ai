"""Explicitly simulated transport; never represents a PSTN call."""
from app.services.telephony.exotel_adapter import ExotelAdapter


class MockAdapter(ExotelAdapter):
    pass
