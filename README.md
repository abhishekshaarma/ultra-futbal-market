# Prediction Market App

A serverless Flask web app for trading in prediction markets. Built with Supabase for backend services and a C++ extension for high-performance order matching.

## What It Does

- User authentication via Supabase Auth  
- Users can browse, trade, and track prediction markets  
- Admins can create and resolve markets  
- Orders matched via C++ extension  
- Users can view balances, positions, transactions, and order books  

## Tech Stack

- **Frontend**: Jinja2 templates (HTML/CSS)
- **Backend**: Flask (Python) with Blueprints
- **Database**: Supabase (PostgreSQL)
- **Auth**: Supabase Auth
- **Order Matching**: C++ extension via Python bindings
- **Deployment**: Vercel (serverless, via `vercel.json`)

## Architecture 

```mermaid
graph TD
  A[UserBrowser] --> B[FlaskApp]
  B --> C[SupabaseDB]
  D[OrderbookCPP] --> B
  B --> E[Jinja2Static]
  B --> F[SupabaseAuth]
