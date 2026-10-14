"""Relay Command Line Interface."""

import argparse
import sys


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="relay",
        description="Relay: Crash-resilient, deterministic customer support AI agent",
    )
    parser.add_argument("--version", action="version", version="relay 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    # worker command
    subparsers.add_parser("worker", help="Start the execution runtime worker")

    # sink command
    subparsers.add_parser("sink", help="Start the external deduplicating sink service")

    # submit command
    submit_parser = subparsers.add_parser("submit", help="Submit a new customer support ticket")
    submit_parser.add_argument("--ticket-id", default="TICKET-001", help="Ticket identifier")
    submit_parser.add_argument(
        "--customer-email", default="customer@example.com", help="Customer email"
    )
    submit_parser.add_argument("--subject", required=True, help="Ticket subject")
    submit_parser.add_argument("--body", required=True, help="Ticket body message")
    submit_parser.add_argument("--invoice-id", default=None, help="Optional invoice identifier")
    submit_parser.add_argument(
        "--amount", type=float, default=0.0, help="Claimed refund amount in USD"
    )
    submit_parser.add_argument(
        "--process-now", action="store_true", help="Process immediately using an inline worker"
    )

    parsed_args = parser.parse_args(args)

    if parsed_args.command is None:
        parser.print_help()
        sys.exit(0)

    if parsed_args.command == "sink":
        from relay.sink import run_service

        run_service()
    elif parsed_args.command == "worker":
        from relay.worker import run_worker

        run_worker()
    elif parsed_args.command == "submit":
        from relay.runtime import ExecutionRuntime

        runtime = ExecutionRuntime()
        ticket_payload = {
            "ticket_id": parsed_args.ticket_id,
            "customer_email": parsed_args.customer_email,
            "subject": parsed_args.subject,
            "body": parsed_args.body,
            "invoice_id": parsed_args.invoice_id,
            "claimed_amount": parsed_args.amount,
        }
        run_id = runtime.create_run(ticket_payload)
        print("Ticket submitted successfully!")
        print(f"Run ID: {run_id}")
        print(f"Ticket: {parsed_args.ticket_id} | Subject: '{parsed_args.subject}'")

        if parsed_args.process_now:
            from relay.worker import Worker

            print("\nProcessing ticket with inline worker...")
            worker = Worker(worker_id="cli-worker", runtime=runtime)
            worker.process_one_run()
            print("Processing complete! Check run status or database for output.")


if __name__ == "__main__":
    main()
