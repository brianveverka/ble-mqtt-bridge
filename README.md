# BLE → MQTT Bridge

Because every BLE device ships with a proprietary app, no API, and just enough documentation to waste your entire weekend.

This fixes that.

---

## What this is

A bridge that:

- Connects to random BLE devices
- Figures out what their packets mean
- Publishes the data to MQTT
- Lets Home Assistant pretend it was supported all along

---

## What this is NOT

- A polished product  
- A beginner-friendly experience  
- Magic  

You will look at hex. You will guess wrong. You will question your life choices.

...unless you don't do it by hand. Which brings us to the actual point.

---

## The intended way: hand it to an AI agent

This is the part that changed everything, and it is now the **recommended**
workflow.

Reverse-engineering a BLE device — scan for it, isolate it, capture packets,
figure out which byte is the temperature, write the template, deploy it to a Pi,
verify the data lands in MQTT — is fiddly, interactive, per-device work. That is
*exactly* what an AI coding agent is good at.

So: give a coding agent (Claude Code, etc.) a shell, point it at a Raspberry Pi
with a Bluetooth adapter, hand it **[AGENT_GUIDE.md](AGENT_GUIDE.md)**, and let it
drive. It scans, isolates the device by toggling it on/off, connects, captures
the notify stream, and decodes the bytes against real fixed points — you put the
probe in ice water, it watches the byte move and solves the scale. Then it writes
the template, adds the device, runs `install.sh` on the Pi over SSH, and confirms
the sensors show up in Home Assistant.

You stay in the loop only for the physical steps it can't do (press the button,
move the probe). It does the hex.

**→ Start here: [AGENT_GUIDE.md](AGENT_GUIDE.md)** — the document you paste to your
agent. It encodes the whole method: identify → capture → calibrate → template →
deploy → verify, plus the Raspberry Pi gotchas (RF-kill, phantom connections,
single-connection devices).

This really is a great way to do it. It's the point of the project now.

---

## Why this exists

Because:

- Vendors don't publish protocols  
- Existing tools almost work, but not quite  
- Writing one-off Python decoders for every device is dumb  

So instead:
**we describe the bytes once, in JSON, and move on with our lives.**

---

## The trick

If a device sends data over BLE, it is not special.

It is just bytes.

And bytes can be decoded.

---

## How it works (mentally)

1. Capture packets
2. Stare at them until patterns emerge
3. Map bytes → values
4. Save that as a template
5. Never think about that device again

---

## Decoder Wizard

The manual escape hatch, for when you'd rather do it yourself than supervise an
agent.

Instead of writing code, you:

- click bytes
- assign meaning
- test guesses

It will feel like cheating. That's the point. (The agent workflow above feels
like more cheating. Also the point.)

---

## Current state

- Works
- Probably breaks in weird edge cases
- Definitely written by someone who got annoyed enough to build it

---

## Usage

- **Recommended:** hand [AGENT_GUIDE.md](AGENT_GUIDE.md) to an AI agent and point
  it at your Pi.
- Manual install / config / Docker: [USAGE.md](USAGE.md).
- On a Raspberry Pi: `sudo bash install.sh install` (the agent does this for you).

---

## Contributing

If you have:

- a weird BLE device
- packet captures
- or the patience to decode nonsense

you're already qualified.

---

## Philosophy

If you can capture it, you can decode it.

If you can decode it, you can automate it.

And if a vendor didn't want that to happen, they shouldn't have used BLE.
