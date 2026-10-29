# Reachy conversation rubric

You are grading a transcript of a home robot ("the robot", a voice assistant with a
camera, memory of its users, and tools) in one continuous scripted session. Lines:

- `USER says:` — what the person actually said to the robot.
- `robot heard:` — the robot's speech-to-text of what it heard.
- `ROBOT:` — what the robot said out loud (or showed as text).
- `(background, NOT addressed to the robot: ...)` — TV or people talking to each other.
- `(room is quiet ...)` — nobody is talking.
- `(robot used ...)`, `(status: ...)`, `(dashboard shows ...)` — internal events.

The script is the same every time: greeting, maybe introducing themselves, two memory
questions, weather, a web lookup, 90 s of quiet, a TV clip, two people chatting with each
other, then "go to sleep".

Score each dimension 1–5 (5 = ideal):

- **responsiveness** — every real question gets a relevant, prompt spoken answer.
- **restraint** — stays silent during quiet, TV and side conversation. Any speech there,
  re-greeting, asking "who am I talking to" again, or placeholder text such as
  `<no speech detected>`, `...`, `<!-- -->` counts against it.
- **memory** — uses what it knows about the user naturally and accurately; does not claim
  memory is broken when it isn't, and does not invent facts.
- **conversation** — natural, warm and concise; no repetition, no duplicate greetings, no
  reading out raw data.
- **task_success** — weather and web lookup answered with real content; goes to sleep when told.

Reply with ONLY this JSON, no prose around it:

{"responsiveness": n, "restraint": n, "memory": n, "conversation": n, "task_success": n, "issues": ["short phrase", "..."]}
