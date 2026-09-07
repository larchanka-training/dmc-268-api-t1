# AGENTS.md

This project is an automated code-review agent for pull and merge requests. It can run automatically when a request is opened or updated, or be triggered on demand via a command, button, or mention.

When triggered, it collects the diff between branches, filters out irrelevant or generated files, splits large changes into manageable chunks, and enriches them with context such as the request's description and existing comments. This context is sent through an orchestration layer to a language model, which reviews the code across several dimensions — security vulnerabilities, logical correctness, performance issues, concurrency/resource safety, and readability — while filtering out likely false positives.

Before anything is published, each finding is checked against the actual diff to confirm it points to a real, changed line; duplicate or low-value comments are removed. The agent then posts a summary of the review, individual comments on the relevant lines (including one-click-apply fixes where possible), and a status update. Replies to its comments are handled as a continued conversation rather than a full re-review.