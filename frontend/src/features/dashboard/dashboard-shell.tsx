"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BookOpen, Bot, Building2, CalendarDays, ChevronDown, ClipboardCheck, FileClock, FileSearch, Files, FileText, GitCompareArrows, Home, Landmark, ListChecks, LogOut, Menu, Settings2, ShieldCheck, ShieldPlus, UploadCloud, X } from "lucide-react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import { GlobalSearch } from "@/features/dashboard/global-search";
import { api } from "@/lib/api/queries";
import { toast } from "sonner";
import { ThemeToggle } from "@/providers/theme-provider";

const SIDEBAR_WIDTH_KEY = "bankease-sidebar-width";
const sidebarWidthListeners = new Set<() => void>();
let sidebarWidthSnapshot: number | null = null;

function readSidebarWidth() {
  if (typeof window === "undefined") return 168;
  if (sidebarWidthSnapshot !== null) return sidebarWidthSnapshot;
  try {
    const stored = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
    return Number.isFinite(stored) && stored >= 168 && stored <= 320 ? stored : 168;
  } catch { return 168; }
}

function subscribeToSidebarWidth(listener: () => void) {
  sidebarWidthListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== SIDEBAR_WIDTH_KEY) return;
    sidebarWidthSnapshot = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    sidebarWidthListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function setSidebarWidth(width: number, persist = false) {
  sidebarWidthSnapshot = width;
  if (persist) {
    try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)); } catch { /* The current session can still use the updated width. */ }
  }
  sidebarWidthListeners.forEach((listener) => listener());
}

function Navigation({ pathname, onNavigate, mobile = false }: { pathname: string; onNavigate?: () => void; mobile?: boolean }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: () => api.reviews(), enabled: Boolean(me.data), staleTime: 30_000 });
  const reviewCount = reviews.data?.reviews.filter((task) => !["approved", "rejected"].includes(task.status)).length ?? 0;
  const linkClass = (href: string) => `group flex min-h-10 items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40 ${pathname === href || (href !== "/dashboard" && pathname.startsWith(href)) ? "bg-blue-600 text-white shadow-sm shadow-blue-600/20" : "text-slate-600 hover:bg-blue-50 hover:text-blue-700"}`;
  const item = (href: string, title: string, Icon: typeof Home) => <Link href={href} onClick={onNavigate} title={title} aria-current={pathname === href ? "page" : undefined} className={linkClass(href)}><Icon size={17} className="shrink-0" /><span className="min-w-0 truncate">{title}</span></Link>;

  return <div className={`workspace-navigation flex h-full min-h-0 flex-col bg-sidebar ${mobile ? "w-[min(300px,86vw)] border-r border-line shadow-xl" : "border-r border-line"}`}>
    <div className="flex h-[68px] shrink-0 items-center gap-3 border-b border-line px-4 xl:px-5">
      <Link onClick={onNavigate} className="flex min-w-0 items-center gap-2.5" href="/dashboard"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-600 text-white"><ShieldCheck size={19} /></span><span className="min-w-0 truncate text-sm font-semibold tracking-tight text-ink">BankEase <span className="text-teal">AI</span></span></Link>
      {mobile && <Button className="ml-auto" variant="ghost" size="icon" onClick={onNavigate} aria-label="Close navigation"><X size={17} /></Button>}
    </div>
    <nav aria-label="Main navigation" className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2.5 py-3 xl:px-3">
      <p className="px-3 pb-2 pt-1 text-[9px] font-bold uppercase tracking-[.15em] text-slate-400">Workspace</p>
      {item("/dashboard", "Dashboard", Home)}
      {item("/dashboard/upload", "Upload document", UploadCloud)}
      {item("/dashboard/documents", "My documents", Files)}
      {item("/dashboard/assistant", "AI Assistant", Bot)}
      <details className="group/nav rounded-lg" open={pathname.startsWith("/dashboard/library/banking") || pathname.startsWith("/dashboard/library/identity")}>
        <summary className="flex min-h-10 cursor-pointer list-none items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40 [&::-webkit-details-marker]:hidden" title="Banking documents"><Landmark size={17} className="shrink-0" /><span className="min-w-0 flex-1 truncate">Banking documents</span><ChevronDown size={13} className="shrink-0 transition group-open/nav:rotate-180" /></summary>
        <div className="ml-7 mt-1 space-y-1 border-l border-blue-100 pl-2"><Link title="Loan agreements" href="/dashboard/library/banking?type=loans" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><FileText size={13} /><span className="truncate">Loan agreements</span></Link><Link title="Bank statements" href="/dashboard/library/banking?type=statements" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><Files size={13} /><span className="truncate">Bank statements</span></Link><Link title="Financial statements" href="/dashboard/library/banking?type=financial" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><FileText size={13} /><span className="truncate">Financial statements</span></Link><Link title="Audit reports" href="/dashboard/library/banking?type=audit" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><ClipboardCheck size={13} /><span className="truncate">Audit reports</span></Link><Link title="KYC forms" href="/dashboard/library/identity" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><ShieldCheck size={13} /><span className="truncate">KYC forms</span></Link></div>
      </details>
      <details className="group/insurance rounded-lg" open={pathname.startsWith("/dashboard/library/insurance")}>
        <summary title="Insurance documents" className="flex min-h-10 cursor-pointer list-none items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40 [&::-webkit-details-marker]:hidden"><ShieldPlus size={17} className="shrink-0" /><span className="min-w-0 flex-1 truncate">Insurance documents</span><ChevronDown size={13} className="shrink-0 transition group-open/insurance:rotate-180" /></summary>
        <div className="ml-7 mt-1 border-l border-blue-100 pl-2"><Link title="My insurance documents" href="/dashboard/library/insurance" onClick={onNavigate} className="flex items-center gap-2 rounded-md px-2 py-2 text-[11px] text-slate-500 hover:bg-blue-50 hover:text-blue-700"><Files size={13} /><span className="truncate">My insurance documents</span></Link></div>
      </details>
      {item("/dashboard/compare", "Compare documents", GitCompareArrows)}
      {item("/dashboard/library/contracts", "Contract explainer", FileText)}
      {item("/dashboard/tools/calendar", "Financial calendar", CalendarDays)}
      {item("/dashboard/tools/obligations", "My obligations", FileClock)}
      {item("/dashboard/tools/meeting-prep", "Meeting preparation", ListChecks)}
      {item("/dashboard/search", "Search across contracts", FileSearch)}
      {item("/dashboard/tools/glossary", "Financial glossary", BookOpen)}
      {item("/dashboard/tools/guides", "Guides & checklists", Building2)}
      {me.data && <>
        <div className="my-3 border-t border-line" />
        {me.data.role === "customer" ? item("/dashboard/reviews", `My reviews${reviewCount ? ` (${reviewCount})` : ""}`, ClipboardCheck) : item("/dashboard/reviews", `Review queue${reviewCount ? ` (${reviewCount})` : ""}`, ClipboardCheck)}
        {item("/dashboard/settings", "Settings", Settings2)}
      </>}
    </nav>
  </div>;
}

export function DashboardShell({ children }: Readonly<{ children: ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, staleTime: 60_000 });
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: () => api.reviews(), enabled: Boolean(me.data), staleTime: 30_000 });
  const [mobileMenu, setMobileMenu] = useState(false);
  const sidebarWidth = useSyncExternalStore(subscribeToSidebarWidth, readSidebarWidth, () => 168);
  const shellRef = useRef<HTMLDivElement>(null);
  const resizingSidebar = useRef(false);
  const signOut = useMutation({ mutationFn: api.logout, onSuccess: () => { queryClient.clear(); router.replace("/login"); }, onError: () => toast.error("Could not sign out. Please try again.") });
  useEffect(() => { if (me.isError) router.replace("/login"); }, [me.isError, router]);
  if (me.isLoading || !me.data) return <main className="grid min-h-screen place-items-center bg-app px-5"><div className="w-full max-w-sm rounded-xl border border-line bg-white p-5 shadow-sm"><div className="h-3 w-28 animate-pulse rounded bg-slate-100" /><div className="mt-4 h-7 w-full animate-pulse rounded bg-slate-100" /><p className="mt-4 text-xs text-muted">{me.isError ? "Opening sign in…" : "Checking your private workspace…"}</p></div></main>;
  const openReviews = reviews.data?.reviews.filter((task) => !["approved", "rejected"].includes(task.status)).length ?? 0;
  const closeMobile = () => setMobileMenu(false);
  const resizeSidebarTo = (clientX: number) => {
    const left = shellRef.current?.getBoundingClientRect().left ?? 0;
    setSidebarWidth(Math.max(168, Math.min(320, Math.round(clientX - left))));
  };
  const onResizePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    resizingSidebar.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onResizePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (resizingSidebar.current) resizeSidebarTo(event.clientX);
  };
  const onResizeKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const next = event.key === "ArrowLeft" ? sidebarWidth - 12
      : event.key === "ArrowRight" ? sidebarWidth + 12
        : event.key === "Home" ? 168
          : event.key === "End" ? 320 : null;
    if (next === null) return;
    event.preventDefault();
    setSidebarWidth(Math.max(168, Math.min(320, next)), true);
  };

  return <div className="dashboard-root min-h-screen bg-app text-ink">
    <div ref={shellRef} className="dashboard-shell-grid grid min-h-screen min-w-0" style={{ "--workspace-sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
      <aside className="sticky top-0 hidden h-screen min-h-0 min-w-0 lg:block"><Navigation pathname={pathname} /></aside>
      <div className="hidden min-h-screen justify-center lg:flex" aria-hidden="false">
        <button type="button" role="separator" aria-orientation="vertical" aria-label="Resize Workspace sidebar" aria-valuemin={168} aria-valuemax={320} aria-valuenow={sidebarWidth}
          className="group flex h-full w-full touch-none cursor-col-resize items-center justify-center outline-none focus-visible:bg-blue-50/60"
          onPointerDown={onResizePointerDown} onPointerMove={onResizePointerMove} onPointerUp={() => { resizingSidebar.current = false; setSidebarWidth(readSidebarWidth(), true); }} onLostPointerCapture={() => { resizingSidebar.current = false; setSidebarWidth(readSidebarWidth(), true); }} onKeyDown={onResizeKeyDown}>
          <span className="h-10 w-0.5 rounded-full bg-line transition group-hover:bg-teal group-focus-visible:bg-teal" />
        </button>
      </div>
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex min-h-[68px] items-center gap-3 border-b border-line bg-white/95 px-3 backdrop-blur sm:px-5 xl:px-6">
          <Button className="shrink-0 lg:hidden" variant="ghost" size="icon" onClick={() => setMobileMenu(true)} aria-label="Open navigation"><Menu size={18} /></Button>
          <GlobalSearch triggerClassName="min-w-0 flex-1" />
          <div className="shrink-0"><ThemeToggle /></div>
          <Link href="/dashboard/reviews" className="relative grid size-10 shrink-0 place-items-center rounded-lg border border-transparent text-slate-600 hover:bg-slate-100" aria-label={"Review tasks" + (openReviews ? ", " + openReviews + " need attention" : "")} title="Review tasks that need attention"><Bell size={18} />{openReviews > 0 && <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-red-500 ring-2 ring-white" />}</Link>
          <details className="group/account relative ml-auto shrink-0">
            <summary className="flex cursor-pointer list-none items-center gap-2.5 rounded-lg p-1.5 hover:bg-slate-50 [&::-webkit-details-marker]:hidden" aria-label="Profile menu">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-blue-100 text-sm font-bold text-blue-800">{(me.data.name.trim()[0] || "U").toUpperCase()}</span>
              <span className="hidden min-w-0 max-w-[220px] sm:block xl:max-w-[260px]">
                <span className="block truncate text-xs font-semibold text-ink">{me.data.name}</span>
                <span className="block truncate text-[10px] text-muted">{me.data.email}</span>
              </span>
              <ChevronDown size={14} className="shrink-0 text-muted" />
            </summary>
            <div className="absolute right-0 top-full z-50 mt-2 hidden w-[min(18rem,calc(100vw-1.5rem))] rounded-xl border border-line bg-panel p-2 shadow-xl group-open/account:block">
              <div className="border-b border-line px-2 py-2">
                <p className="break-words text-xs font-semibold text-ink">{me.data.name}</p>
                <p className="mt-0.5 break-all text-[10px] text-muted">{me.data.email}</p>
                <p className="mt-1 text-[10px] capitalize text-muted">{me.data.role.replaceAll("_", " ")}</p>
              </div>
              <Link href="/dashboard/settings" className="mt-1 flex items-center gap-2 rounded-lg px-2 py-2 text-xs text-ink hover:bg-panel-raised"><Settings2 size={15} />Account settings</Link>
              <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs text-red-700 hover:bg-red-50"><LogOut size={15} />{signOut.isPending ? "Signing out…" : "Sign out"}</button>
            </div>
          </details>
        </header>
        <main className="mx-auto min-h-0 min-w-0 w-full max-w-[1440px] flex-1 px-4 py-5 sm:px-6 sm:py-7 xl:px-7">{children}</main>
        <footer className="px-4 pb-5 text-center text-[10px] leading-5 text-slate-500 sm:px-6 xl:px-7">For informational and educational use only. Verify important details against your original document.</footer>
      </div>
    </div>
    {mobileMenu && <div className="fixed inset-0 z-40 bg-slate-950/30 lg:hidden" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeMobile(); }}><aside className="h-full"><Navigation pathname={pathname} mobile onNavigate={closeMobile} /></aside></div>}
  </div>;
}
