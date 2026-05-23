window.HEDI_SUPPORT = window.HEDI_SUPPORT || {
  email: "jimmershere@gmail.com",
  phone: "+14342426591",
  brand: "Team HEDI Support"
};

window.HEDI_AI_SCOUT = window.HEDI_AI_SCOUT || {
  promptVersion: "turbohedi-support-v1",
  lightContext: true,
  toolOrder: ["upload-playbook", "job-review", "ack-guide", "ticket-intake"],
  routing: {
    mode: "rules",
    provider: "openrouter",
    primaryModel: "deepseek/deepseek-chat-v3-0324:free",
    fallbackModel: "openai/gpt-4.1-mini",
    apiBase: "https://openrouter.ai/api/v1",
    cacheFriendly: true,
    notes: "Scaffold only for now. Rules stay primary until a real server-side assistant lane exists."
  },
  contextProfile: {
    allowedFields: ["page", "uploadedBy", "tradingPartner", "jobId", "status", "validation"],
    maxTickets: 6
  }
};
