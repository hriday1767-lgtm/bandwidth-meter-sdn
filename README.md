# Bandwidth Meter and Traffic Analyzer (UDP + SDN)

**Course:** Computer Networks — SDN Socket Programming Mini Project (Project #26)
**Goal (from the problem statement):** develop a UDP-based traffic measurement
application, and compare application-level bandwidth measurements against SDN
(OpenFlow) flow statistics for the same traffic, across multiple load levels.

This repo implements the full pipeline: Mininet topology → Ryu SDN controller
→ UDP socket sender/receiver → automated multi-load experiment → comparison
report. It is written to satisfy every bullet in the assignment brief and
every line item in the rubric (mapping at the bottom of this file).

---

## 1. Architecture

```
                     s1 ===primary, 5 Mbit=== s2
                    /                            \
                  h1 (sender)                     h2 (receiver)
                    \                            /
                     s1 --backup, 20 Mbit-- s3 -- s2

                        Ryu SDN controller (root namespace)
                        - OpenFlow 1.3 to s1/s2/s3 (control plane)
                        - Unix-socket alert listener (see §4)
```

* **h1** runs `socket_app/sender.py` — generates UDP measurement traffic at a
  controlled packet rate.
* **h2** runs `socket_app/receiver.py` — counts packets, computes loss,
  computes application-level throughput.
* **s1/s2/s3** are OVS switches under a Ryu controller. Two switch-disjoint
  paths exist between h1 and h2: a narrow **primary** link (intentionally
  capped at 5 Mbit so it can be congested) and a wider **backup** path
  through s3.
* The controller polls OpenFlow flow statistics on s1 every second
  (network-level view of the same traffic) and **dynamically reroutes**
  the flow onto the backup path when it detects congestion — either from
  its own polling, or from a direct alert sent by the receiver app.

This gives you, in one run: an application-level measurement, an
independent network-level (SDN) measurement of the *same* traffic, and a
system that visibly reacts to congestion — which is what the rubric's
"Dynamic SDN Functionality" and "Challenging Scenario / Robustness" items
are asking for.

## 2. Repository layout

```
bandwidth-meter-sdn/
├── topology/topo.py              Mininet topology (primary + backup path)
├── controller/sdn_controller.py  Ryu app: forwarding, polling, rerouting, alerts
├── socket_app/sender.py          UDP traffic generator
├── socket_app/receiver.py        UDP receiver / bandwidth meter
├── experiments/run_experiment.py Automates a multi-load-level test run
├── analysis/compare.py           Lines up app-level vs SDN-level measurements
└── results/                      CSVs land here (git-ignored except .gitkeep)
```

## 3. Environment setup

Follow your course's Mininet install guide first (Ubuntu 20.04/22.04 VM with
`sudo apt install mininet`, verified with `sudo mn` + `pingall`).

This project uses **os-ken** (OpenStack's actively-maintained fork of Ryu,
same API, `os_ken.*` import path instead of `ryu.*`) instead of the original
`ryu` package. `ryu` was last released in 2021 and its own GitHub page now
says outright that it's unmaintained: its legacy `setup.py` calls
`easy_install.get_script_args`, an attribute that modern `setuptools` no
longer exposes, so `pip install ryu` fails to build on any current
toolchain — this isn't a Python-version problem you can work around with
`--no-build-isolation` or pinning `setuptools`; the installer script is
simply broken against every recent `setuptools`. `os-ken` avoids this
entirely because it ships a normal prebuilt wheel (no build-from-source
step), and it defaults to real Python threads rather than `eventlet`,
sidestepping `eventlet`'s own history of lagging behind new Python releases.

```bash
sudo apt install python3-pip python3-venv -y
python3 -m venv ~/ryu-venv
source ~/ryu-venv/bin/activate
pip install os-ken
```

One catch, confirmed by actually installing and running it: the published
PyPI package for `os-ken` 4.2.2 is **missing its `osken-manager` console
script** (the `os_ken/cmd/` package that script needs isn't in the wheel or
the sdist, even though it exists in the upstream git repo). Use
`run_controller.py` in this project's root instead of `osken-manager` — it's
a ~20-line stand-in using the same public `AppManager.run_apps()` API the
real script would have called. If a future `os-ken` release restores the
console script, you can switch to `osken-manager controller/sdn_controller.py`
with no other changes needed.

No third-party packages are needed for `socket_app/`, `topology/topo.py`
(other than Mininet, which the install guide already covers), or
`analysis/compare.py` — they're all standard library.

## 4. Design note: why an AF_UNIX socket for controller alerts?

`socket_app/receiver.py` can notify the controller directly when it sees
high packet loss (`controller/sdn_controller.py`'s alert listener). This is
deliberately implemented as an **AF_UNIX datagram socket**
(`/tmp/bwm_controller_alert.sock`), not UDP/IP. Mininet hosts each get their
own *network* namespace (their own private `127.0.0.1`/IP stack), but not a
separate filesystem — so a Unix socket path is visible to both a Mininet
host process and the controller process (which runs in the root namespace)
with no extra NAT/routing setup. A UDP/IP alert would need an
`mininet.nodelib.NAT` node and route configuration to cross that namespace
boundary — unnecessary complexity for what is, architecturally, the same
kind of out-of-band control signal as the OpenFlow channel itself.

If you'd rather demonstrate a "real" IP-socket control channel for extra
marks on Socket–SDN Integration, that NAT setup is the next step; it's out
of scope for this base implementation but worth mentioning in your report
as a discussed limitation.

## 5. Verifying the topology assumptions (do this once)

The controller hard-codes MAC addresses, datapath IDs, and port numbers for
speed. Before your first real run, confirm they match your Mininet version:

```bash
sudo python3 topology/topo.py
```

At startup it prints each switch's port mapping and each host's MAC/IP.
Compare against the constants at the top of `controller/sdn_controller.py`
(`H1_MAC`, `H2_MAC`, `DPID_S1/S2/S3`, `S1_PORT_*`, etc.) and edit them if
your Mininet assigns things differently. Type `exit` in the `mininet>`
prompt to tear the network down.

## 6. Running a manual test (D1 — initial testing & demo)

Terminal 1 (project root, controller):
```bash
source ~/ryu-venv/bin/activate
python3 run_controller.py controller.sdn_controller
```

Terminal 2 (project root, topology):
```bash
sudo python3 topology/topo.py
mininet> h2 python3 socket_app/receiver.py --port 5005 --idle-timeout 5 \
             --alert-sock /tmp/bwm_controller_alert.sock &
mininet> h1 python3 socket_app/sender.py --dst 10.0.0.2 --port 5005 \
             --rate 800 --duration 20 --size 1024 --flow-id 1
mininet> h1 ping -c 3 10.0.0.2
```

Watch the controller terminal: you should see `Switch N connected`, then
(if the 800 pps / 1024 B load — about 6.5 Mbps — exceeds the 5 Mbit primary
link) a `REROUTE -> backup` log line partway through the run.

After the sender finishes, check `results/receiver_summary.csv` (loss %,
throughput) and `results/controller_stats.csv` (SDN-measured rate over
time, with reroute events marked).

## 7. Running the full experiment (D2 — complete functionality + load sweep)

Instead of the manual steps above, `experiments/run_experiment.py` builds
the topology itself, starts the receiver on h2, and sweeps the sender on h1
through three load levels (light / moderate / heavy — see `LOAD_LEVELS` in
that file) automatically, each as its own flow id:

```bash
# terminal 1
source ~/ryu-venv/bin/activate
python3 run_controller.py controller.sdn_controller
# terminal 2
sudo python3 experiments/run_experiment.py
```

This directly produces the "analyze different traffic loads" deliverable:
`results/receiver_summary.csv` will have one row per load level, and
`results/controller_stats.csv` will show the reroute triggering under the
moderate/heavy loads but not under light load — your evidence that the
threshold-based dynamic behaviour is real, not cosmetic.

## 8. Comparing application-level vs SDN-level measurements

```bash
python3 analysis/compare.py \
    --receiver results/receiver_timeseries_flow2.csv \
    --controller results/controller_stats.csv \
    --out results/comparison_flow2.csv
```

Repeat for each `flow<N>` you care about. This is the "compare application
and network measurements" deliverable — the printed table and CSV both show
app-measured Mbps next to SDN-measured Mbps per second, with reroute events
annotated.

## 9. The challenging / robustness scenario

Use the **heavy** load level (or the manual test with a rate clearly above
5 Mbit) as your "congestion" scenario:
1. Show `receiver_summary.csv` loss % is non-trivial while on the primary path.
2. Show the controller log / `controller_stats.csv` reroute event.
3. Show loss dropping (or throughput recovering) in the timeseries after the
   reroute timestamp — this is your "system responds appropriately" evidence
   for the rubric's Challenging Scenario / Robustness item.

You can additionally demonstrate the app-triggered path specifically:
lower `CONGESTION_THRESHOLD_BPS` in the controller (or raise the sender
rate further) so the receiver's own loss detector (`--loss-alert-pct`,
default 5%) fires *before* the periodic poll would — the controller log
will show `app-triggered` instead of `poll-triggered` as the reroute reason,
proving the socket-to-SDN integration is doing real work, not just sitting
next to the polling logic.

## 10. Documentation & GitHub deliverable

The assignment requires the topology, source code, configuration, test
cases and instructions to be shared as a GitHub repo. Suggested repo
contents beyond what's here:
- A short report (can be a Word doc — ask if you'd like this turned into
  one) with: problem statement, architecture diagram, screenshots of a
  `run_controller.py` run showing a reroute event, the comparison table/plot from
  §8, and the loss/throughput numbers from at least two load levels.
- `results/` from an actual run (commit the CSVs so graders can see them
  without re-running Mininet).
- This README as-is, or trimmed to taste.

## 11. Rubric mapping (from the course rubric)

| Rubric item | Where it's satisfied |
|---|---|
| Problem Understanding & Architecture | §1 architecture, this file overall |
| TCP/UDP Socket Implementation | `socket_app/sender.py`, `socket_app/receiver.py` |
| Mininet + SDN Implementation | `topology/topo.py`, `controller/sdn_controller.py` |
| Socket–SDN Integration | §4 + the alert listener in `sdn_controller.py` / `receiver.py` |
| Initial Testing & Demo | §6 |
| Complete Application Functionality | §7, all bullet points from the brief (packet counts, throughput, SDN stats, loss, comparison, multiple loads) |
| Dynamic SDN Functionality | Congestion-based rerouting in `sdn_controller.py` (`_evaluate_congestion`, `_reroute`) |
| Challenging Scenario / Robustness | §9 |
| Performance Evaluation & Comparison | `analysis/compare.py`, §8 |
| Documentation, Final Demo & Viva | This README + §10 |

---

### Known simplifications (worth naming in your report, not hiding)

- The controller hard-codes topology facts (MACs/DPIDs/ports) rather than
  discovering them dynamically — reasonable for a fixed two-path mini
  topology, but wouldn't scale to arbitrary topologies (a production
  controller would use LLDP-based topology discovery).
- Rerouting only actively manages the h1→h2 flow on s1; it's a
  proof-of-concept of SDN-driven traffic engineering, not a general
  multi-flow load balancer.
- One-way latency isn't computed from the embedded send timestamp because
  Mininet hosts don't have synchronized clocks by default (they share the
  host clock, so in practice this works acceptably on one VM, but treat any
  latency number as indicative only if you choose to extend the receiver
  to report it).
