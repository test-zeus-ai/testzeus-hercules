unused_packages=$(jq -r '.unused[]' unused_packages.json) && \
if [ -n "$unused_packages" ]; then \
  echo "Removing unused packages: $unused_packages"; \
  poetry remove $unused_packages; \
else \
  echo "No unused packages found."; \
fi
