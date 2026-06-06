
# AI Job Analysis Platform

An AI-powered platform that helps job seekers analyze job postings, evaluate company stability, and make informed career decisions by combining multiple data sources and AI analysis.

## 🚀 Current Status: Phase 0 — real LLM, honest gaps

The pipeline now performs **real LLM calls** against whichever provider key is
present (Anthropic / OpenAI / Featherless — auto-detected, overridable with
`LLM_PROVIDER`). Without a key, the app runs in clearly-labelled demo mode and
returns sample data — never silently. See `.env.example`.

### What Works Now
- ✅ Real LLM calls (Anthropic / OpenAI / Featherless) with retries
- ✅ Demo mode is visibly labelled (no fake "Production Mode" badge)
- ✅ Job posting requirement extraction
- ✅ Company stability briefing with explicit "data not available" labels
- ✅ Heuristic salary + cost-of-living estimate, labelled as ESTIMATE
- ✅ Final recommendation report
- ✅ Streamlit UI, progress callbacks, thread-safe cache
- ✅ End-to-end demo-mode smoke test

### Honest Gaps (Phase 1+)
- 🔄 No live company news / layoffs feed yet — placeholder, not a clean bill of health
- 🔄 Salary & COL figures come from internal heuristics, not Glassdoor/Numbeo
- ❌ Employee-review fabrication has been REMOVED — culture section is now an
  inference briefing with research pointers, not invented reviews
- ❌ Resume / ATS analysis (headline feature from the vision doc) not built
- ❌ No persistence, auth, or application tracking

## 🎯 Roadmap: Production-Ready Features

### Phase 1: Real Data Integration
- [ ] **Company Financial Data**: Integrate with APIs like Alpha Vantage, Yahoo Finance, or SEC filings
- [ ] **Salary Benchmarking**: Connect to Glassdoor, PayScale, or Levels.fyi APIs
- [ ] **Cost of Living**: Integrate Numbeo, BestPlaces, or similar APIs
- [ ] **Company Reviews**: Access Glassdoor, Indeed, or Blind APIs
- [ ] **News & Layoffs**: Integrate news APIs and layoff tracking services

### Phase 2: Enhanced Analysis
- [ ] **CV Tailoring**: Auto-generate customized resumes based on job requirements
- [ ] **Cover Letter Generation**: Create personalized cover letters
- [ ] **Interview Preparation**: Generate potential interview questions and answers
- [ ] **Skills Gap Analysis**: Identify missing skills and suggest learning resources

### Phase 3: Application Tracking
- [ ] **Application Database**: Track where and when users applied
- [ ] **Status Management**: Monitor application progress (applied, interview, rejection, offer)
- [ ] **Follow-up Reminders**: Automated reminders for application follow-ups
- [ ] **Analytics Dashboard**: Personal job search analytics and insights
- [ ] **Document Management**: Store tailored CVs and cover letters per application

### Phase 4: Advanced Features  
- [ ] **Job Alert System**: Automated job matching and notifications
- [ ] **Network Analysis**: LinkedIn integration for connection insights
- [ ] **Market Trends**: Industry-specific hiring trends and forecasts
- [ ] **Negotiation Assistant**: Salary negotiation strategies and talking points

## 🛠️ Technology Stack

- **Frontend**: Streamlit
- **Backend**: Python with LangChain/LangGraph
- **AI Models**: Support for multiple LLMs (OpenAI, DeepSeek, Qwen)
- **Caching**: In-memory caching system
- **Architecture**: Agent-based orchestration pattern

## 🚀 Quick Start

1. **Clone and Setup**:
   ```bash
   # The project runs on Replit - click "Run" to start
   # or manually run:
   streamlit run app.py
   ```

2. **Environment Variables**:
   ```bash
   # Add your API keys to .env file:
   OPENAI_API_KEY=your_openai_key_here
   # Add other API keys as you integrate real data sources
   ```

3. **Usage**:
   - Select analysis model (Fast or Detailed)
   - Fill in basic job details
   - Paste the full job description
   - Click "Analyze Job" and wait for results

## 📁 Project Structure

```
├── agents/                 # AI agents for different analysis tasks
│   ├── job_analyzer.py     # Job posting analysis
│   ├── company_analyzer.py # Company research
│   ├── salary_analyzer.py  # Compensation analysis
│   └── report_generator.py # Final recommendations
├── tools/                  # External API integrations (currently mock)
│   ├── job_tools.py        # Job parsing tools
│   ├── company_tools.py    # Company data tools  
│   └── salary_tools.py     # Salary benchmark tools
├── utils/                  # Utility functions
│   ├── llm.py             # LLM interaction
│   └── cache.py           # Caching system
├── app.py                 # Main Streamlit application
└── README.md              # This file
```

## 🔒 Security & Best Practices

- ✅ No hardcoded API keys or secrets
- ✅ Environment variable usage for configuration
- ✅ Input validation and error handling
- ✅ Modular, maintainable code structure
- ✅ Comprehensive logging for debugging

## 🤝 Contributing

This project is ready for collaboration and open-source contributions:

1. **Current State**: Fork and improve the mock data implementations
2. **API Integration**: Help integrate real external APIs
3. **UI/UX**: Enhance the Streamlit interface
4. **Testing**: Add comprehensive test coverage
5. **Documentation**: Improve code documentation and user guides

## 📄 License

MIT License - feel free to use this project as a foundation for your own job analysis tools.

## 🔮 Vision

Transform job searching from a manual, time-consuming process into an AI-powered, data-driven experience that helps candidates:
- Make informed career decisions
- Stand out with tailored applications  
- Track and optimize their job search process
- Negotiate better compensation packages
- Build long-term career strategies

---

**Ready to contribute or integrate real APIs?** Check out the issues tab or reach out to discuss collaboration opportunities!
