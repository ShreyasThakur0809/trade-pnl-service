# Render Deployment Guide

This guide will help you deploy the trade-pnl-service to Render.com with a free PostgreSQL database.

## Prerequisites

- A [Render.com](https://render.com) account (free tier available)
- GitHub account with the repository pushed

## Step-by-Step Deployment

### 1. Prepare Your Repository

Your repository is already configured with:
- `render.yaml` - Render blueprint configuration
- `pyproject.toml` - Updated with PostgreSQL driver (`psycopg2-binary`)
- `.gitignore` - Excludes local database files
- Auto-loading sample data on startup

### 2. Deploy to Render

#### Option A: Using Render Blueprint (Recommended)

1. Go to [https://dashboard.render.com](https://dashboard.render.com)
2. Click **"New +"** → **"Blueprint"**
3. Connect your GitHub account if not already connected
4. Select the `ShreyasThakur0809/trade-pnl-service` repository
5. Render will automatically detect `render.yaml`
6. Review the configuration:
   - **Web Service**: `trade-pnl-service`
   - **Database**: `trade-pnl-db` (PostgreSQL)
7. Click **"Apply"** to start deployment

#### Option B: Manual Setup

1. **Create PostgreSQL Database**
   - Go to **"New +"** → **"PostgreSQL"**
   - Name: `trade-pnl-db`
   - Database: `tradepnl`
   - User: `tradepnl_user`
   - Region: Choose nearest to you
   - Click **"Create Database"**

2. **Create Web Service**
   - Go to **"New +"** → **"Web Service"**
   - Connect your GitHub repository
   - Name: `trade-pnl-service`
   - Environment: `Python`
   - Build Command: `pip install -e ".[data]"`
   - Start Command: `uvicorn tradepnl.main:app --host 0.0.0.0 --port $PORT`
   - Add Environment Variables:
     - `DATABASE_URL`: (Copy from your PostgreSQL database)
     - `PYTHON_VERSION`: `3.13`
     - `ALLOW_SHORT`: `false`
     - `RECON_TOLERANCE`: `0.00000001`
   - Click **"Create Web Service"**

### 3. Wait for Deployment

- Render will build and deploy your application
- This typically takes 2-5 minutes
- Watch the logs in the Render dashboard
- You'll see "Deploy successful" when done

### 4. Access Your Application

Once deployed, Render will provide:
- **Application URL**: `https://trade-pnl-service.onrender.com` (or similar)
- **API Docs**: `https://your-app.onrender.com/docs`
- **Health Check**: `https://your-app.onrender.com/health`

### 5. Verify Deployment

Test your deployed application:

```bash
# Health check
curl https://your-app.onrender.com/health

# Check positions (should have sample data loaded)
curl https://your-app.onrender.com/v1/positions

# Check PnL
curl https://your-app.onrender.com/v1/pnl
```

## What Happens Automatically

The application will:
1. Create PostgreSQL tables on first startup
2. Load sample trade data (26 fills across 4 venues)
3. Load mark prices for PnL calculations
4. Run reconciliation checks
5. Be ready to use immediately

## Free Tier Limits

Render's free tier includes:
- **Web Service**: 750 hours/month (enough for always-on)
- **PostgreSQL**: 90 days free, then ~$7/month
- **Bandwidth**: 100GB/month
- **Build time**: 15 minutes/month

## Environment Variables

You can configure these in Render dashboard:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | Auto-set | PostgreSQL connection string |
| `API_KEY` | unset | Set to require authentication |
| `ALLOW_SHORT` | `false` | Allow short positions |
| `RECON_TOLERANCE` | `0.00000001` | Reconciliation tolerance |

## Monitoring

- View logs in Render dashboard
- Check metrics (CPU, memory, response time)
- Set up alerts for errors or high latency

## Updating Your Application

1. Push changes to GitHub
2. Render automatically detects and redeploys
3. Watch deployment logs
4. New version goes live when successful

## Troubleshooting

### Build Fails
- Check Python version matches (`>=3.11`)
- Verify `pyproject.toml` is correct
- Check build logs in Render dashboard

### Database Connection Error
- Verify `DATABASE_URL` is set correctly
- Check database is running
- Ensure database and web service are in same region

### Sample Data Not Loading
- Check `data/sample_trades.json` exists in repo
- Review startup logs
- Manually trigger seed script if needed

### Application Not Responding
- Check web service status in dashboard
- Review recent logs for errors
- Verify all environment variables are set

## Cost Estimates

After free trial:
- **Web Service**: Free (750 hours/month)
- **PostgreSQL**: ~$7/month
- **Total**: ~$7/month for production use

## Next Steps

1. Set up custom domain (optional)
2. Configure API key for authentication
3. Set up monitoring and alerts
4. Configure backup strategy for database
5. Review and adjust environment variables

## Support

- Render Documentation: https://render.com/docs
- Render Community: https://community.render.com
- GitHub Issues: https://github.com/ShreyasThakur0809/trade-pnl-service/issues
