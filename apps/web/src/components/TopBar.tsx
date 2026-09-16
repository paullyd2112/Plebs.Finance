"use client";

import { useRouter } from "next/navigation";
import { Settings, LogOut } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import SearchBar from "@/components/SearchBar";

export default function TopBar() {
  const router = useRouter();
  const supabase = createClient();

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <header className="h-14 flex-shrink-0 bg-black/40 backdrop-blur-xl border-b border-white/[0.06] flex items-center justify-between px-5 gap-4 relative z-30">
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <SearchBar />
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        <a
          href="/dashboard/settings"
          className="inline-flex items-center gap-1.5 text-xs text-zinc-500 hover:text-white rounded-lg px-2 py-1.5 hover:bg-white/[0.04] transition-colors"
          aria-label="Settings"
        >
          <Settings className="h-4 w-4" />
          <span className="hidden sm:inline">Settings</span>
        </a>
        <button
          onClick={signOut}
          className="inline-flex items-center gap-1.5 text-xs text-zinc-500 hover:text-white rounded-lg px-2 py-1.5 hover:bg-white/[0.04] transition-colors"
          aria-label="Sign out"
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </div>
    </header>
  );
}
