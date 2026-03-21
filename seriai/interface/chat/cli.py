"""
Simple CLI chat interface for MAZLUM.
For development and quick testing.
"""
import sys


def run_cli(brain):
    """Run interactive CLI chat."""
    print("MAZLUM CLI - 'q' ile çıkış")
    print("-" * 40)

    while True:
        try:
            user_input = input("\nSen: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("q", "quit", "exit", "çık"):
                print("Görüşürüz.")
                break

            response = brain.process(
                user_text=user_input,
                context={"source": "cli"},
            )
            print(f"\nMAZLUM: {response.text}")
            print(f"  [{response.model_used} | {response.input_tokens}+{response.output_tokens} tokens | {response.latency_ms}ms]")

        except KeyboardInterrupt:
            print("\nÇıkış.")
            break
        except Exception as e:
            print(f"\nHata: {e}")
