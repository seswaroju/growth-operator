"""Cron scheduler process entrypoint — see docs/25-implementation-starter-kit/12-background-jobs.md.

Placeholder until MVP-028 (scheduler process); keeps the container alive for `make dev`.
"""

import time


def main() -> None:
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
