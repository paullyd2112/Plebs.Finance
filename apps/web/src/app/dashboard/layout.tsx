import { redirect } from "next/navigation";
import { getUser } from "@/lib/user";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import BottomNav from "@/components/BottomNav";
import TickerBar from "@/components/TickerBar";
import WalletProvider from "@/providers/WalletProvider";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await getUser();
  if (!user) redirect("/login");

  return (
    <div className="flex h-screen bg-background text-white overflow-hidden">
      {/* Sidebar — desktop only */}
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0 relative z-10">
        <TopBar />
        <TickerBar showStatus={false} />
        <div className="px-4 py-1 border-b border-white/[0.06] text-center flex-shrink-0">
          <p className="text-[10px] text-zinc-600">
            For informational purposes only. Not financial advice. Past performance is not indicative of future results.
          </p>
        </div>
        <main className="flex-1 overflow-y-auto pb-16 lg:pb-0">
          <WalletProvider>{children}</WalletProvider>
        </main>
      </div>

      {/* Bottom nav — mobile only */}
      <BottomNav />
    </div>
  );
}
