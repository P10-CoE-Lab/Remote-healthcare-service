# What Is This Simulator?

## The Simple Version

We are building a **pretend person generator**.

It runs on a laptop. It pretends to be a real person — either a factory worker
doing their shift, or a cardiac patient being monitored at home. It sends out
the same kind of data that real sensors would send. The rest of the system
(the database, the dashboard, the alerts) cannot tell the difference between
this simulator and a real person wearing real sensors.

---

## Why Do We Need It?

We have two problems that the simulator solves.

**Problem 1 — We cannot make people sick on demand.**

For the healthcare POC we need to show a client what happens when a patient's
heart rate suddenly spikes, or when their blood oxygen drops dangerously low.
We cannot ask a real person to have a cardiac event during a demo. We also
cannot wait around hoping a real patient has one at the right moment.

For the worker safety POC we need to show what happens when a worker gets
dangerously fatigued after 6 hours, or when someone falls on the factory floor.
Again, we cannot stage a real accident.

The simulator lets us say "show tachycardia now" or "trigger a fall event now"
at the press of a button.

**Problem 2 — We need to test our system before the hardware is ready.**

Building and calibrating physical sensors takes time. While that work is
happening, the backend, the database, the alert logic, and the dashboard all
need to be built and tested. The simulator provides realistic fake data so
all of that work can happen in parallel, without waiting for a working
physical device.

---

## What It Is NOT

- It is not a medical device
- It is not trying to perfectly replicate real ECG signals at a clinical level
- It is not a replacement for real sensors in the final product
- It is not something a doctor would use to diagnose a patient

It is a development and demo tool. Nothing more.

---

## The Two Projects It Serves

### Project 1 — Worker Safety Wearable

A factory worker wears two devices. A belt unit that tracks movement and
posture. A wrist unit that tracks heart rate and body temperature.

The simulator pretends to be both devices for one or more workers. It can
show a worker having a normal productive shift, or it can show a worker
gradually getting tired, starting to bend dangerously, and eventually
triggering a safety alert.

**Sensors it fakes:**
- Body posture angle (how much the person is bending)
- Movement and activity (walking, standing still, bending, falling)
- Heart rate (beats per minute)
- Skin temperature

**Conditions it can simulate:**
- Normal healthy work
- Gradual fatigue building up over a shift
- Unsafe posture (bending too much for too long)
- Sudden fall
- Worker not moving for too long (possible collapse)
- Heat stress (body temperature rising dangerously)

### Project 2 — Remote Healthcare Monitoring

A cardiac patient at home wears a small device on their wrist or chest. It
monitors their heart continuously and sends alerts to a caregiver if something
looks wrong.

The simulator pretends to be this device for one or more patients. It can
show a patient resting normally, or it can show a patient going into
tachycardia, or their oxygen dropping, or their heart rhythm becoming irregular.

**Sensors it fakes:**
- Heart rate (beats per minute)
- Blood oxygen level / SpO2 (percentage)
- Basic ECG signal (the electrical pattern of the heartbeat)
- Signal quality indicator

**Conditions it can simulate:**
- Normal resting heart rhythm
- Tachycardia (heart beating too fast, above 100 bpm)
- Bradycardia (heart beating too slow, below 50 bpm)
- Low SpO2 (blood oxygen dropping, possible breathing problem)
- Irregular heart rhythm (beats coming unevenly)
- Motion artifact (the reading looks bad because the person moved)

---

## What the Two Projects Share

Even though the two projects sound very different — one is about factory
workers, the other is about heart patients — they have a lot in common at
the simulator level.

Both involve a person whose body state changes over time. Both need to show
normal conditions and then gradually or suddenly move into a dangerous
condition. Both send data over the same communication channel (MQTT). Both
store data in the same database (InfluxDB). Both show results on the same
dashboard tool (Grafana).

This means we can build one simulator that serves both, not two separate
simulators.

---

## How It Works in Plain Terms

Think of the simulator as having three parts.

**Part 1 — The Person (Persona)**

Before anything starts, you tell the simulator what kind of person it is
pretending to be. A 45-year-old male welder who has been doing this job for
10 years will have different normal heart rate, different posture patterns,
and different thresholds than a 28-year-old female office worker.

The persona sets the baseline. What is normal for this person. What their
sensors would read on a completely healthy, unremarkable day.

**Part 2 — The Story (Scenario)**

A scenario is the script the simulator follows. It has a beginning, a middle,
and an end. It describes what happens to the person over time.

For example a factory worker scenario might say:

- For the first 3 hours everything is normal
- Between hour 3 and hour 5 the worker gradually gets more tired
- At hour 5 there is a posture violation
- At hour 6 the worker stops moving for 2 minutes

A healthcare scenario might say:

- Patient is resting normally for 10 minutes
- Heart rate starts climbing slowly
- At 15 minutes full tachycardia episode begins
- At 18 minutes heart rate returns to normal

Scenarios are written in a simple text file that even a non-programmer can
read and edit. This means the person running the demo can adjust the story
before a client meeting without touching any code.

**Part 3 — The Demo Controller**

This is a simple web page that runs on the same laptop. It has buttons and
controls. You can load a scenario, start it, pause it, speed it up, or
manually trigger an event like a fall or a tachycardia episode at any moment.

A sales person running a client demo uses this. They do not need to know
anything about code or sensors. They just click buttons.

---

## What the Demo Looks Like

The simulator runs on a laptop. Grafana (the dashboard) also runs on the same
laptop or a simple server. The client watches the Grafana screen either in
person or on a video call.

The sales person loads the scenario, starts it, and narrates what is happening
as the metrics change on screen. When they want to show something dramatic they
press a button — fall event, tachycardia episode, low oxygen alarm — and the
dashboard reacts in real time.

The whole demo for one scenario takes about 5 to 8 minutes. You can run
multiple scenarios back to back to show different situations.

---

## What We Are NOT Building Yet (Future)

The following things are deliberately left out of the first version to keep
it simple and achievable for one person:

- BLE zone detection (worker entering a hazard area)
- Computer vision (camera-based PPE detection)
- Full clinical-grade ECG waveform synthesis
- Mobile app
- Multi-worker simultaneous simulation at large scale

These can be added later once the core simulator is working.

---

## Summary in One Paragraph

We are building a simulator that pretends to be a person — either a factory
worker or a cardiac patient — and sends realistic health and safety data to
our system over the same channel that real sensors would use. It follows a
script that describes what happens to that person over time, including normal
conditions and dangerous conditions. It has a simple control panel so anyone
can run it during a client demo. One simulator serves both projects because
both projects use the same data pipeline. The first version focuses on getting
the story and the demo right. Waveform-level accuracy and advanced sensors
come later.