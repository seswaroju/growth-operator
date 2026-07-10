"""Stream consumer process entrypoint.

See docs/25-implementation-starter-kit/06-backend-plan.md (events/).

Placeholder until MVP-026 (consumer framework); keeps the container alive for `make dev`.
"""

import time


def main() -> None:
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
