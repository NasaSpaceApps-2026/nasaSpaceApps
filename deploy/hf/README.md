---
title: Ares Atlas
emoji: 🔴
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Mars EVA planning at Jezero crater with an AI copilot on real NASA data
---

# Ares Atlas

NASA Space Apps 2026, "Interplanetary Survival Guide: Martian Map". A research and exploration tool on real NASA data
(HiRISE DTM, Perseverance traverse, CRISM-derived mineral layers) for planning Mars EVAs at Jezero crater, with an
AI copilot that plans routes, finds sites and drives the rover. Every number cites its source or is labelled "assumed".

Open the Space URL: the viewer, the copilot dock (right side), the rover and the map overlays all run in this one container.
The copilot calls the DeepSeek API using the Space's `DEEPSEEK_API_KEY` secret; chat is rate limited per visitor.
