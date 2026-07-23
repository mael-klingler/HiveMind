// Supabase Edge Function: Rate Limiting
// Deploy with: supabase functions deploy rate-limit
// Uses the check_rate_limit RPC function defined in 00002_rls_and_functions.sql

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

const MAX_REQUESTS_PER_MINUTE = 30

serve(async (req: Request) => {
  const clientIp = req.headers.get("x-forwarded-for") || req.headers.get("cf-connecting-ip") || "unknown"
  
  // Check rate limit via Postgres RPC
  const supabaseUrl = Deno.env.get("SUPABASE_URL")!
  const supabaseKey = Deno.env.get("SUPABASE_SERVICE_KEY")!
  
  const response = await fetch(`${supabaseUrl}/rest/v1/rpc/check_rate_limit`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "apikey": supabaseKey,
      "Authorization": `Bearer ${supabaseKey}`,
    },
    body: JSON.stringify({
      p_client_ip: clientIp,
      p_max_requests: MAX_REQUESTS_PER_MINUTE,
    }),
  })
  
  const result = await response.json()
  const allowed = result[0]?.allowed ?? true
  const remaining = result[0]?.remaining ?? MAX_REQUESTS_PER_MINUTE
  
  if (!allowed) {
    return new Response(JSON.stringify({ detail: "Rate limit exceeded" }), {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        "X-RateLimit-Remaining": "0",
      },
    })
  }
  
  return new Response(JSON.stringify({ allowed: true, remaining }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "X-RateLimit-Remaining": String(remaining),
    },
  })
})