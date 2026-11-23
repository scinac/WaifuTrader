# Trading Terminal

Assumptions below are inferred from the FSD, because the owner asked the builder to use its judgment.

## Product

A self-hosted trading terminal for its owner and a few friends in Austria. Each person trades their own broker accounts. It is private software, reached only through Tailscale, and it is not a financial service.

## Users and use

Retail traders watching markets for long sessions, often in the evening, on a desktop monitor, and checking in from a phone. They need to trust within a second whether a price is live, stale or delayed.

## Register

Operate. Dark, dense, pro-terminal UI built around one symbol detail screen (FSD: User interface).

## Principles

- Data truth comes first. Stale, delayed, disconnected and paper states are always visible.
- Money stays as decimal strings until it is drawn. Times are stored in UTC and shown in Europe/Vienna.
- Predictions are never presented as certain.
- Nothing appears that the backend cannot do yet. No dead Buy buttons.

## Stack

React, TypeScript, Vite, TanStack Query, Zustand, KLineChart.
