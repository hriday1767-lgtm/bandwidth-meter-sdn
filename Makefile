cat << 'EOF' > Makefile
# Bandwidth Meter and Traffic Analyzer -- run everything with make.
#
#   make all            clean -> controller -> load sweep -> comparison -> baseline
#                       -> two-sender test -> summary -> stop controller
#   make help           list every step
#
# Run as your NORMAL user (not with sudo). Steps that need root call sudo
# themselves. Activate your venv first if os-ken lives in one:
#   source ~/ryu-venv/bin/activate
#
# Tunables:   make baseline REPEATS=2

REPEATS ?= 1
SOCK    := /tmp/bwm_controller_alert.sock
PIDFILE := results/controller.pid

.PHONY: help all check clean controller sweep compare baseline baseline-table \
        multiclient multiclient-table events summary stop shots

help:
	echo "make check             verify tools and files are in place"
	echo "make clean             delete ALL old results and stale Mininet state"
	echo "make controller        start the SDN controller in the background"
	echo "make sweep             original light/moderate/heavy load sweep"
	echo "make compare           app-vs-SDN comparison for flows 1-3"
	echo "make baseline          static vs dynamic, every load   (REPEATS=$(REPEATS))"
	echo "make baseline-table    table + chart for the baseline run"
	echo "make multiclient       two simultaneous senders        (REPEATS=$(REPEATS))"
	echo "make multiclient-table table + chart for the two-sender run"
	echo "make events            controller MODE / REROUTE / STATIC events"
	echo "make summary           receiver summary + events + both tables"
	echo "make stop              stop the controller and clean Mininet"
	echo "make shots             show every result one screen at a time (for screenshots)"
	echo "make all               everything above, in order"

check:
	command -v mn >/dev/null || { echo "Mininet (mn) not found"; exit 1; }
	python3 -c "import os_ken" 2>/dev/null || { echo "os_ken missing - activate your venv first"; exit 1; }
	for f in run_controller.py controller/sdn_controller.py topology/topo.py \
	         socket_app/sender.py socket_app/receiver.py \
	         experiments/run_experiment.py experiments/run_baseline.py \
	         experiments/run_multiclient.py experiments/ctl.py \
	         analysis/compare.py analysis/baseline_compare.py; do \
	  [ -f $$f ] || { echo "Missing file: $$f"; exit 1; }; done
	grep -q "__main__" experiments/ctl.py || { echo "experiments/ctl.py lost its __main__ guard"; exit 1; }
	echo "check OK"

clean: stop
	sudo mn -c >/dev/null 2>&1 || true
	sudo rm -rf results results_old_* run_all.log
	sudo find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -f $(SOCK)
	mkdir -p results
	echo "clean OK - all old data removed"

controller:
	mkdir -p results
	rm -f $(SOCK)
	nohup python3 run_controller.py controller.sdn_controller > results/controller.log 2>&1 & echo $$! > $(PIDFILE)
	for i in $$(seq 1 20); do [ -S $(SOCK) ] && break; sleep 1; done; \
	  [ -S $(SOCK) ] || { echo "controller did not start - see results/controller.log"; exit 1; }
	python3 experiments/ctl.py dynamic
	echo "controller running (log: results/controller.log)"

sweep:
	printf 'exit\n' | sudo python3 experiments/run_experiment.py
	sudo chown -R $$(whoami):$$(whoami) results/ || true
	sudo mn -c >/dev/null 2>&1 || true

compare:
	for n in 1 2 3; do \
	  echo "=========== FLOW $$n ==========="; \
	  python3 analysis/compare.py --receiver results/receiver_timeseries_flow$$n.csv \
	     --controller results/controller_stats.csv --out results/comparison_flow$$n.csv; done

baseline:
	sudo mn -c >/dev/null 2>&1 || true
	sudo python3 experiments/run_baseline.py --repeats $(REPEATS)
	sudo chown -R $$(whoami):$$(whoami) results/ || true

baseline-table:
	python3 analysis/baseline_compare.py

multiclient:
	sudo mn -c >/dev/null 2>&1 || true
	sudo python3 experiments/run_multiclient.py --repeats $(REPEATS)
	sudo chown -R $$(whoami):$$(whoami) results/ || true

multiclient-table:
	python3 analysis/baseline_compare.py --kind multiclient

events:
	python3 analysis/show_events.py

summary:
	echo "=========== RECEIVER SUMMARY ==========="; python3 analysis/show_csv.py results/receiver_summary.csv
	echo; echo "=========== CONTROLLER EVENTS ==========="; python3 analysis/show_events.py
	echo; echo "=========== STATIC vs DYNAMIC ==========="; python3 analysis/baseline_compare.py --no-plot
	echo; echo "=========== TWO SENDERS ==========="; python3 analysis/baseline_compare.py --kind multiclient --no-plot

stop:
	-[ -f $(PIDFILE) ] && kill $$(cat $(PIDFILE)) 2>/dev/null
	-pkill -f run_controller.py 2>/dev/null
	-sudo mn -c >/dev/null 2>&1
	-rm -f $(PIDFILE) $(SOCK)

shots:
	clear; echo "SHOT 1: receiver summary"; python3 analysis/show_csv.py results/receiver_summary.csv; read -p ">> screenshot, then Enter " x
	for n in 1 2 3; do clear; echo "SHOT: comparison flow $$n"; python3 analysis/show_csv.py results/comparison_flow$$n.csv; read -p ">> screenshot, then Enter " x; done
	clear; echo "SHOT: controller events"; python3 analysis/show_events.py; read -p ">> screenshot, then Enter " x
	clear; echo "SHOT: static vs dynamic"; python3 analysis/baseline_compare.py --no-plot; read -p ">> screenshot, then Enter " x
	clear; echo "SHOT: two senders"; python3 analysis/baseline_compare.py --kind multiclient --no-plot; read -p ">> screenshot, then Enter " x
	echo "Charts: xdg-open results/baseline_*/baseline_comparison.png results/multiclient_*/baseline_comparison.png"

all: check clean controller sweep compare baseline baseline-table multiclient multiclient-table summary stop
	@echo "ALL DONE - results are in ./results"
EOF
