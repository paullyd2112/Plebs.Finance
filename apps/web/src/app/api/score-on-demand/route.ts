import { NextResponse } from "next/server";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { requireUser } from "@/lib/user";

export const maxDuration = 30;

const Body = z.object({
  asset_type: z.enum(["stock", "crypto"]),
  identifier: z.string().min(1).max(20),
});

const DATA_SERVICE_URL = process.env.DATA_SERVICE_URL || "https://bloomberg-term-clone-production.up.railway.app";
const DAILY_LIMIT = 10;

export async function POST(request: Request) {
  const user = await requireUser().catch(() => null);
  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => null);
  const parsed = Body.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  const { asset_type, identifier } = parsed.data;
  const upperId = identifier.toUpperCase();

  const supabase = createClient();

  // Check for a recent signal first (avoid unnecessary scoring)
  const fourHoursAgo = new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString();
  const { data: existing } = await supabase
    .from("signals")
    .select("*")
    .eq("asset_type", asset_type)
    .eq("identifier", upperId)
    .eq("is_backtest", false)
    .gte("created_at", fourHoursAgo)
    .order("created_at", { ascending: false })
    .limit(1);

  if (existing && existing.length > 0) {
    return NextResponse.json({ status: "cached", signal: existing[0] });
  }

  // Check daily usage limit
  const todayStart = new Date();
  todayStart.setUTCHours(0, 0, 0, 0);

  const { count } = await supabase
    .from("on_demand_scoring_log")
    .select("id", { count: "exact", head: true })
    .eq("user_id", user.id)
    .gte("created_at", todayStart.toISOString());

  if ((count ?? 0) >= DAILY_LIMIT) {
    return NextResponse.json(
      { error: `Daily limit of ${DAILY_LIMIT} on-demand scores reached` },
      { status: 429 },
    );
  }

  if (!DATA_SERVICE_URL) {
    return NextResponse.json(
      { error: "Scoring service not configured" },
      { status: 503 },
    );
  }

  // Call the data service to score the asset
  try {
    const resp = await fetch(`${DATA_SERVICE_URL}/score-asset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_type, identifier: upperId }),
    });

    const result = await resp.json();

    // Log the usage
    await supabase.from("on_demand_scoring_log").insert({
      user_id: user.id,
      asset_type,
      identifier: upperId,
    });

    if (result.status === "skipped") {
      return NextResponse.json({
        status: "no_signal",
        reason: "No actionable data available for this ticker",
      });
    }

    return NextResponse.json({ status: "ok", signal: result.signal });
  } catch {
    return NextResponse.json(
      { error: "Scoring service unavailable" },
      { status: 503 },
    );
  }
}
