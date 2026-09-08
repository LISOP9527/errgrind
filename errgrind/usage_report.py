"""Read local usage telemetry without initializing a provider or database."""
import argparse
import json

from .llm.usage import log_path, summarize


def main():
    parser = argparse.ArgumentParser(description='汇总本地模型用量日志（仅统计服务端已报告的 token，不估算费用）')
    parser.add_argument('--days', type=int, help='只统计最近 N 天；默认全部保留日志')
    args = parser.parse_args()
    if args.days is not None and args.days < 1:
        parser.error('--days 必须大于 0')
    print(json.dumps(summarize(log_path(), args.days), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
