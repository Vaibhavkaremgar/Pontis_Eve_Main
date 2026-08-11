// Central mock data for Eve (candidate-side AI) — mirrors the shape of the eventual API.

export const MOCK_USER_PROFILE = {
  name: "Akshitha S",
  email: "akshithamona@gmail.com",
  avatar:
    "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&auto=format&fit=crop&q=80",
  headline:
    "Co-Founder of Viral Bug | Automation Specialist | Building practical AI systems that eliminate repetitive business work",
  location: "Hyderabad, India",
  bio:
    "A results-driven Business Development and Growth Manager with over 5 years of experience in client acquisition and revenue growth across India and Canada. They have a proven track record as an ex-founder in the AI automation space, having successfully onboarded enterprise clients and generated $1M in revenue. They specialize in B2B sales strategy, strategic partnerships, and CRM-driven growth, with full work authorization in both India and Canada.",
  strength: "Strong",
  strengthPercent: 88,
  isOpenToMatches: true,
  targetRoles: ["Business Development Manager", "Growth Manager", "AI Automation Specialist"],
  compensation: "₹15-30L / $120-160k CAD",
  keySkills: [
    "B2B Sales",
    "Client Acquisition",
    "Revenue Growth",
    "CRM Strategy",
    "AI Automation",
    "Strategic Partnerships",
    "Enterprise Sales",
    "Product Marketing",
  ],
  experience: [
    {
      id: "exp-1",
      title: "Co-Founder",
      company: "Viral Bug",
      dates: "2023 — Present",
      description:
        "Building practical AI systems that automate repetitive business workflows for SMB and enterprise clients across India and Canada.",
    },
    {
      id: "exp-2",
      title: "Growth Manager",
      company: "ABC Growth",
      dates: "2021 — 2023",
      description:
        "Owned end-to-end client acquisition funnel; scaled ARR from $200k to $1M through outbound and partner-led motions.",
    },
    {
      id: "exp-3",
      title: "Business Development Executive",
      company: "Nova",
      dates: "2019 — 2021",
      description:
        "Ran a full-cycle B2B SaaS sales motion for enterprise CRM and marketing automation software.",
    },
  ],
  education: [
    {
      id: "edu-1",
      degree: "B.B.A. in Marketing",
      institution: "Osmania University",
      dates: "2015 — 2018",
    },
  ],
};

export const MOCK_ONBOARDING_STEPS = [
  { id: "step-1", title: "Talk to Eve", completed: true, active: false },
  { id: "step-2", title: "Complete Profile", completed: false, active: true },
  { id: "step-3", title: "Search Jobs", completed: false, active: false },
];

export const MOCK_CHATS = [
  {
    id: "sys-1",
    isDayDivider: true,
    label: "Yesterday",
  },
  {
    id: "sys-2",
    isCallSummary: true,
    label: "You had a call with Eve",
    time: "6:57",
  },
  {
    id: "msg-1",
    sender: "eve",
    content:
      "Hi Akshitha — it was great chatting on the call just now. I've got your CV and LinkedIn in front of me, which gives us a massive head start on building your profile.",
  },
  {
    id: "msg-2",
    sender: "eve",
    content:
      "This profile is what Pontis uses to match you with roles and, more importantly, it's what hiring managers read when deciding whether to reach out. Since you're targeting those enterprise SaaS roles, we want this to really highlight the revenue growth and client acquisition numbers we talked about.",
  },
  {
    id: "msg-3",
    sender: "eve",
    content:
      "I've already got a first draft of your timeline started with your roles at Viral Bug, ABC Growth, and Nova — want to go through it together and make sure it captures your best wins?",
  },
  {
    id: "msg-4",
    sender: "user",
    content: "yes",
  },
  {
    id: "sys-3",
    isActionSummary: true,
    label: "Performed 3 actions",
    count: 3,
  },
];

export const MOCK_AVAILABLE_JOBS = [
  {
    id: "job-101",
    title: "Business Development Manager — B2B SaaS",
    company: "KC Overseas Education",
    logo: "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=100&auto=format&fit=crop&q=80",
    location: "Hyderabad, TS, IN & Remote",
    salary: "₹15,00,000 — ₹30,00,000",
    matchScore: "96%",
    description:
      "Sell an AI-native CRM and automation platform incubated by a 27-year industry leader — startup ownership with enterprise stability.",
    aboutRole:
      "ARO.AI is an AI-first product being built by KC Overseas Education, a global leader in international education with 27+ years of operations across 30+ countries. We're building an AI-powered CRM that helps study abroad consultants and higher education institutions streamline student recruitment, admissions, lead management, and counsellor productivity.",
    tracked: false,
  },
  {
    id: "job-102",
    title: "Business Process Automation Manager",
    company: "Ignite Human Capital",
    logo: "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?w=100&auto=format&fit=crop&q=80",
    location: "San Diego, CA & Remote",
    salary: "$120,000 — $150,000",
    matchScore: "92%",
    description:
      "Lead automation workflows for enterprise recruitment operations across a global talent network.",
    aboutRole:
      "Architecting high-throughput processing engines for recruitment pipelines across global talent networks.",
    tracked: true,
  },
  {
    id: "job-103",
    title: "Principal Enterprise Account Executive",
    company: "Stellaris Data",
    logo: "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=100&auto=format&fit=crop&q=80",
    location: "New York, NY (Remote)",
    salary: "$180,000 — $220,000 OTE",
    matchScore: "89%",
    description:
      "Drive strategic SaaS partnerships with Fortune 500 financial institutions.",
    aboutRole:
      "Direct ownership of tier-1 enterprise accounts across multi-region AWS and Kubernetes integrations.",
    tracked: false,
  },
  {
    id: "job-104",
    title: "Head of Growth — AI Automation",
    company: "Loopwork AI",
    logo: "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=100&auto=format&fit=crop&q=80",
    location: "Toronto, ON (Hybrid)",
    salary: "$140,000 — $170,000 CAD",
    matchScore: "94%",
    description:
      "Own top-of-funnel and product-led growth for an AI workflow automation platform serving mid-market ops teams.",
    aboutRole:
      "Loopwork AI is building an end-to-end automation layer for finance and ops teams. You'll own paid, content, and partner motions with a small full-stack marketing pod.",
    tracked: false,
  },
];

export const MOCK_RECENT_ACTIVITY = [
  { id: "ra-1", company: "ElevenLabs", role: "Account Executive" },
  { id: "ra-2", company: "AppDirect", role: "Account Executive" },
  { id: "ra-3", company: "Apty Software Pvt. Ltd.", role: "Enterprise AE" },
  { id: "ra-4", company: "Apty", role: "Partner Growth Manager" },
  { id: "ra-5", company: "Harvey", role: "Account Executive" },
];

export const MOCK_DOCUMENTS = [
  {
    id: "doc-1",
    name: "Akshitha_Resume_2026.pdf",
    size: "245 KB",
    uploadedAt: "Yesterday",
    type: "Resume",
    verifiedByEve: true,
  },
  {
    id: "doc-2",
    name: "Growth_Playbook_Case_Study.pdf",
    size: "1.2 MB",
    uploadedAt: "3 days ago",
    type: "Portfolio",
    verifiedByEve: true,
  },
  {
    id: "doc-3",
    name: "BBA_Transcript.pdf",
    size: "512 KB",
    uploadedAt: "Last week",
    type: "Education",
    verifiedByEve: false,
  },
];

export const QUICK_ACTIONS = [
  "Search for roles",
  "Update preferences",
  "Salary help",
  "CV help",
];
