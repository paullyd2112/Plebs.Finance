# Plebs Open Source — Remaining Steps

**Branch:** `claude/plebs-open-source-lp36rg`

Everything on the original checklist is done. What's left is yours to do on GitHub.

---

## Manual steps

1. **Merge this branch into `main`.**
2. **Make the repo public** — GitHub → Settings → General → Danger Zone → Change visibility.
3. **Add repo metadata** — description, topics (`crypto`, `trading`, `prediction-markets`,
   `nextjs`, `claude-ai`, `polymarket`), and the plebs.finance link.
4. **Add screenshots** to the README. The README points at the live site for now; inline
   images of the dashboard and predictions tab would land better on GitHub.
5. **Delete this file** once the above is done.

---

## Deliberately not done

- **Database cleanup.** The `profiles` table still has `stripe_customer_id`,
  `stripe_subscription_id`, `tier`, `billing_interval`, `trial_ends_at`, `referral_code`,
  and `referred_by`; the `referrals` and `redemption_codes` tables still exist; and
  `get_dashboard_signals()` in `001_initial_schema.sql` still carries tier-delay logic.
  No code reads any of it. Dropping the columns is a breaking migration for anyone who
  already ran the old schema, so it was left alone.

- **Git history.** The removed business docs (pricing tiers, MRR targets, marketing
  strategy) are still reachable in history. Verified that no `.env` file was ever committed
  and no credential-shaped strings are in any tracked file, so nothing here is a secret —
  just old planning notes. Squashing to a fresh initial commit is the only way to remove
  them, at the cost of the full development history.

- **Stock re-enablement.** Stock code is intact behind `ENABLE_STOCK_SCORING`. See CLAUDE.md.
