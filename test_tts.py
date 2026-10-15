import threading

import tts


def test_speakable_drops_code_and_markdown_and_cuts_long_answers():
    text = "**Paris** é a capital. ```py\nprint(1)\n``` Fim! " + "Frase longa de enchimento. " * 40
    parts = tts.speakable(text)
    assert parts[0] == "Paris é a capital." and parts[1] == "Fim!"
    assert "print" not in " ".join(parts) and sum(map(len, parts)) <= tts.SPEAK_MAX + 40


def test_say_plays_every_sentence_and_stops_on_cancel():
    played, cancel = [], threading.Event()
    sp = tts.Speaker(synth=lambda s: ([0.0] * 10, 24000), play=lambda a, r: played.append(len(a)))
    sp.say("Um. Dois. Tres.", cancel)
    assert played == [10, 10, 10]

    def synth(s):
        cancel.set()  # cancelled while the first sentence was being made
        return [0.0], 24000
    played.clear()
    tts.Speaker(synth=synth, play=lambda a, r: played.append(1)).say("Um. Dois.", cancel)
    assert played == []


def test_unknown_language_falls_back_to_english():
    assert tts.Speaker("xx").voice == "af_heart"


def test_release_drops_the_model_until_the_next_use():
    sp = tts.Speaker("pt")
    sp._k = object()
    sp.release()
    assert sp._k is None
