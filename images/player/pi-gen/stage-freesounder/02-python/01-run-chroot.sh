#!/bin/bash -e
# One venv for freesounderd and the BLE provisioning service (Python 3.13).
rm -rf /opt/freesounder/control-api /opt/freesounder/player-agent
python3 -m venv /opt/freesounder/venv
PIP=/opt/freesounder/venv/bin/pip
WHEELS=/opt/freesounder/wheels
if [ -d "$WHEELS" ]; then
	${PIP} install --no-index --find-links="$WHEELS" \
		hatchling packaging pathspec pluggy trove-classifiers editables tomlkit
	for svc in freesounderd ble-provisioning; do
		${PIP} install --no-index --find-links="$WHEELS" --no-build-isolation "/opt/freesounder/${svc}"
	done
else
	for svc in freesounderd ble-provisioning; do
		${PIP} install "/opt/freesounder/${svc}"
	done
fi
rm -rf "$WHEELS"
/opt/freesounder/venv/bin/python -c "import freesounderd, freesounderd.app, freesounder_ble_provisioning; print(freesounderd.__version__, freesounder_ble_provisioning.__version__)"
