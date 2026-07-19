import asyncio
from novelizer.telemetry.bus import TelemetryBus


def test_subscriber_receives_published_items_in_order():
    bus = TelemetryBus()
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    assert q.get_nowait() == "a"
    assert q.get_nowait() == "b"


def test_full_queue_drops_oldest_and_never_blocks_publisher():
    bus = TelemetryBus(maxsize=2)
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")  # queue full: "a" is dropped, publish returns immediately
    assert q.get_nowait() == "b"
    assert q.get_nowait() == "c"


def test_slow_subscriber_does_not_affect_other_subscribers():
    bus = TelemetryBus(maxsize=1)
    slow = bus.subscribe()
    fast = bus.subscribe()
    bus.publish("a")
    bus.publish("b")  # slow's queue overflows (drop-oldest); fast also capped at 1
    assert slow.get_nowait() == "b"
    assert fast.get_nowait() == "b"


def test_unsubscribe_stops_delivery():
    bus = TelemetryBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("a")
    assert q.empty()


def test_unsubscribe_unknown_queue_is_a_noop():
    bus = TelemetryBus()
    bus.unsubscribe(asyncio.Queue())  # must not raise
