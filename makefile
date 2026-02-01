.PHONY: run install

run: install
	python3 src/main.py -t $(CURDIR)/test/ex.ctri

install:

