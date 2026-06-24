REPO    := robertogeekACN/CyberTranscoder
PYZ     := cybertrans.pyz
FORMULA := tap/Formula/cybertrans.rb

VERSION ?= $(error Set VERSION, e.g.  make release VERSION=v1.0.0)

.PHONY: build release clean

# Rebuild the .pyz zipapp from cybertrans.py
build:
	@cp cybertrans.py build/src/__main__.py
	@python3 -m zipapp build/src -o $(PYZ) -p '/usr/bin/env python3'
	@echo "Built $(PYZ)  ($$(du -sh $(PYZ) | cut -f1))"

# Create a GitHub release and keep the formula in sync.
# Usage: make release VERSION=v1.2.3
release: build
	$(eval _SHA := $(shell shasum -a 256 $(PYZ) | awk '{print $$1}'))
	$(eval _URL := https://github.com/$(REPO)/releases/download/$(VERSION)/$(PYZ))
	@sed -i '' \
	    -e 's|url ".*"|url "$(_URL)"|' \
	    -e 's|sha256 ".*"|sha256 "$(_SHA)"|' \
	    $(FORMULA)
	@echo "Formula updated → url/sha256 for $(VERSION)"
	@gh release create $(VERSION) $(PYZ) \
	    --repo $(REPO) \
	    --title "CyberTrans $(VERSION)" \
	    --notes "Release $(VERSION)"
	@echo ""
	@echo "Next: commit and push the tap repo"
	@echo "  cd tap && git add Formula/cybertrans.rb && git commit -m 'bump to $(VERSION)' && git push"

clean:
	@rm -f $(PYZ)
