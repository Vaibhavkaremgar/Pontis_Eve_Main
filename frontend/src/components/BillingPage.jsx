import React from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function formatDate(value) {
  return value ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date(value)) : "—";
}

export default function BillingPage({ candidateId, onUpgrade }) {
  const [billing, setBilling] = React.useState(null);
  const [error, setError] = React.useState(false);
  React.useEffect(() => {
    if (!candidateId) return;
    setError(false);
    axios.get(`${API}/candidate/${candidateId}/billing`).then(({ data }) => setBilling(data)).catch(() => setError(true));
  }, [candidateId]);
  if (!billing && !error) return <div className="h-full flex items-center justify-center text-sm text-[#777571]">Loading billing…</div>;
  if (error) return <div className="h-full flex items-center justify-center text-sm text-[#777571]">Billing details are unavailable right now.</div>;
  const active = billing.status === "Active";
  return <div className="h-full overflow-y-auto eve-scroll p-5 sm:p-7" data-testid="billing-page">
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-black/[0.06] pb-5">
      <div><p className="text-[12px] font-semibold uppercase tracking-[.12em] text-[#7B6FB8]">Billing</p><h2 className="mt-1 text-2xl font-semibold tracking-tight">Your subscription</h2></div>
      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${active ? "bg-[#E6F3EA] text-[#277443]" : "bg-[#EEEAF8] text-[#62578F]"}`}>{billing.status}</span>
    </div>
    <section className="mt-5 rounded-2xl border border-[#E7E3F0] bg-[#FAF9FD] p-5">
      <p className="text-sm text-[#5D5D5A]">Current plan</p><p className="mt-1 text-xl font-semibold">{active ? "₹3,000 / 3 months" : "Free"}</p>
      {active ? <div className="mt-5 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2"><div><p className="text-[#777571]">Subscription start</p><p className="mt-1 font-medium">{formatDate(billing.subscription_start)}</p></div><div><p className="text-[#777571]">Subscription expiry</p><p className="mt-1 font-medium">{formatDate(billing.subscription_expiry)}</p></div></div> : <><p className="mt-3 text-sm leading-6 text-[#5D5D5A]">Unlock daily job matches, priority visibility, and free mock interviews when shortlisted.</p><button data-testid="billing-upgrade-btn" onClick={onUpgrade} className="mt-5 rounded-xl bg-[#62578F] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#514875]">Upgrade</button></>}
    </section>
    <section className="mt-5"><h3 className="text-base font-semibold">Payment details</h3><dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2"><div><dt className="text-[#777571]">Payment status</dt><dd className="mt-1 font-medium capitalize">{billing.payment_status || "Not started"}</dd></div><div><dt className="text-[#777571]">Razorpay transaction ID</dt><dd className="mt-1 break-all font-medium">{billing.payment_id || "—"}</dd></div></dl></section>
    <section className="mt-6"><h3 className="text-base font-semibold">Payment history</h3><div className="mt-3 overflow-hidden rounded-xl border border-black/[0.07]">{billing.history?.length ? billing.history.map((item, index) => <div key={item.razorpay_order_id || index} className="grid gap-1 border-b border-black/[0.06] p-3 text-sm last:border-0 sm:grid-cols-3"><span className="font-medium capitalize">{item.status}</span><span>₹{((item.amount_paise || 0) / 100).toLocaleString("en-IN")}</span><span className="break-all text-[#777571]">{item.razorpay_payment_id || item.receipt || item.razorpay_order_id}</span></div>) : <p className="p-4 text-sm text-[#777571]">No payments yet.</p>}</div></section>
  </div>;
}
