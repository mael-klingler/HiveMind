// Supabase Edge Function: GitLab Webhook Validation
// Deploy with: supabase functions deploy webhook-validate

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 })
  }

  const gitlabToken = req.headers.get("x-gitlab-token")
  const expectedToken = Deno.env.get("GITLAB_WEBHOOK_SECRET")

  // Verify GitLab webhook token
  if (expectedToken && gitlabToken !== expectedToken) {
    console.error("Invalid GitLab webhook token")
    return new Response(JSON.stringify({ detail: "Invalid webhook token" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })
  }

  try {
    const payload = await req.json()
    
    // Forward the webhook to the HiveMind orchestrator
    const orchestratorUrl = Deno.env.get("ORCHESTRATOR_URL") || "http://orchestrator:8080"
    const event = req.headers.get("x-gitlab-event") || "unknown"
    
    // Map GitLab events to HiveMind ticket creation
    if (event === "Issue Hook" || event === "Merge Request Hook") {
      const forwardResponse = await fetch(`${orchestratorUrl}/webhooks/gitlab`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Gitlab-Event": event,
          "X-Gitlab-Token": gitlabToken || "",
          "X-Webhook-Verified": "true",
        },
        body: JSON.stringify(payload),
      })
      
      const forwardData = await forwardResponse.json()
      return new Response(JSON.stringify(forwardData), {
        status: forwardResponse.status,
        headers: { "Content-Type": "application/json" },
      })
    }
    
    return new Response(JSON.stringify({ status: "ignored", event }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  } catch (error) {
    console.error("Webhook processing error:", error)
    return new Response(JSON.stringify({ detail: "Invalid request body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }
})