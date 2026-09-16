import { NextResponse } from "next/server";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { requireUser } from "@/lib/user";

const Body = z.object({
  asset_type: z.enum(["stock", "crypto"]),
  identifier: z.string().min(1).max(100).transform((s) => s.toUpperCase()),
});

export async function POST(req: Request) {
  let user;
  try { user = await requireUser(); } catch {
    return NextResponse.json({ error: "Unauthenticated" }, { status: 401 });
  }

  const parsed = Body.safeParse(await req.json());
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid input" }, { status: 400 });
  }

  const supabase = createClient();

  const { data, error } = await supabase
    .from("watchlist")
    .insert({ user_id: user.id, ...parsed.data })
    .select()
    .single();

  if (error) {
    if (error.code === "23505") {
      return NextResponse.json({ error: "Already in watchlist" }, { status: 409 });
    }
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }

  return NextResponse.json(data, { status: 201 });
}

export async function DELETE(req: Request) {
  let user;
  try { user = await requireUser(); } catch {
    return NextResponse.json({ error: "Unauthenticated" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const id = searchParams.get("id");
  if (!id) return NextResponse.json({ error: "Missing id" }, { status: 400 });

  const supabase = createClient();
  const { error } = await supabase
    .from("watchlist")
    .delete()
    .eq("id", id)
    .eq("user_id", user.id);

  if (error) return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  return NextResponse.json({ deleted: true });
}
