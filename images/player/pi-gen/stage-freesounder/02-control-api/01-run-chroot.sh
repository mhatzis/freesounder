#!/bin/bash -e
# Install the control API into a dedicated Python 3 venv.
# Raspberry Pi OS Trixie ships Python 3.13, which music-assistant-client requires.
cd /opt/freesounder/control-api
python3 -m venv /opt/freesounder/control-api/.venv
PIP=/opt/freesounder/control-api/.venv/bin/pip
${PIP} install --upgrade pip
if [ -d /opt/freesounder/control-api/wheels ]; then
	# hatchling is a build-time dep (pyproject build-system.requires) and must
	# be present in the offline wheel cache; then skip build isolation.
	${PIP} install --no-index --find-links=/opt/freesounder/control-api/wheels \
		hatchling packaging pathspec pluggy trove-classifiers editables tomlkit
	${PIP} install --no-index --find-links=/opt/freesounder/control-api/wheels \
		--no-build-isolation /opt/freesounder/control-api
else
	${PIP} install /opt/freesounder/control-api
fi
rm -rf /opt/freesounder/control-api/wheels
# Confirm the package imported (do not launch the server here).
/opt/freesounder/control-api/.venv/bin/python -c "import freesounder_control_api, music_assistant_client; print(freesounder_control_api.__version__)"
