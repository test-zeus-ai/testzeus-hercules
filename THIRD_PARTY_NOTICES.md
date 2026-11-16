# Third-party notices

Hercules ships the following third-party code unmodified, under its own licence.

| Component | Version | Licence | Location | Source |
|---|---|---|---|---|
| axe-core | 4.10.3 | MPL-2.0 | `testzeus_hercules/quality/vendor/axe/` (licence files alongside) | https://github.com/dequelabs/axe-core/tree/v4.10.3 |
| Manrope (font, via @fontsource/manrope) | 5.3.0 | OFL-1.1 | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/sharanda/manrope |
| JetBrains Mono (font, via @fontsource/jetbrains-mono) | 5.3.0 | OFL-1.1 | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/JetBrains/JetBrainsMono |
| Silkscreen (font, via @fontsource/silkscreen) | 5.3.0 | OFL-1.1 | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/googlefonts/silkscreen |
| React (`react`) | 19.2.0 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/facebook/react |
| React DOM (`react-dom`) | 19.2.0 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/facebook/react |
| Scheduler (`scheduler`) | 0.27.0 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/facebook/react |
| TanStack Query (`@tanstack/react-query, @tanstack/query-core`) | 5.90.12 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/TanStack/query |
| wouter (`wouter`) | 3.9.0 | Unlicense | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/molefrog/wouter |
| regexparam (`regexparam`) | 3.0.0 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/lukeed/regexparam |
| mitt (`mitt`) | 3.0.1 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/developit/mitt |
| use-sync-external-store (`use-sync-external-store`) | 1.7.0 | MIT | Bundled into `testzeus_hercules/ui/static/assets/` by the UI build | https://github.com/facebook/react |

## Optional external tools

Hercules does not ship these programs. It runs them when you install them yourself.

| Tool | Version | Licence | Used for | Source |
|---|---|---|---|---|
| nuclei | any; `hercules scan --install` downloads 3.11.1 | MIT | Security scans (`hercules scan`, security steps). Its templates are downloaded by nuclei itself and have their own licence (MIT). | https://github.com/projectdiscovery/nuclei |
