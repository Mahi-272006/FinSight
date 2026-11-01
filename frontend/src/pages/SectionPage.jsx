import {
  Activity,
  Bookmark,
  Building2,
  FileText,
  SearchCheck,
  Scale,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState, PageIntro, Panel } from '../components/ui.jsx'

const sectionDetails = {
  companies: {
    icon: Building2,
    eyebrow: 'COMPANY RESEARCH',
    limitation: 'The backend provides company and financial-history endpoints. Company-page integration is planned after this foundation review.',
  },
  anomalies: {
    icon: Activity,
    eyebrow: 'INVESTIGATION SIGNALS',
    limitation: 'The backend provides persisted anomaly signals and deterministic recomputation. Signal exploration will be connected in the next frontend stage.',
  },
  thesis: {
    icon: Scale,
    eyebrow: 'THESIS MONITORING',
    limitation: 'The backend supports thesis creation, deterministic assessments and append-only history. Thesis workflows are not connected in this foundation.',
  },
  research: {
    icon: SearchCheck,
    eyebrow: 'EVIDENCE-LED RESEARCH',
    limitation: 'The backend supports synchronous research runs and optional saved reports. The research form and agent workflow will follow foundation review.',
  },
  reports: {
    icon: FileText,
    eyebrow: 'SAVED RESEARCH',
    limitation: 'The backend supports paginated report listing and report detail. Report browsing will be integrated after this foundation review.',
  },
  watchlist: {
    icon: Bookmark,
    eyebrow: 'COMPANIES TO FOLLOW',
    limitation: 'The backend supports a shared watchlist with create, list and delete operations. Watchlist management will be integrated next.',
  },
}

function SectionPage({ path, title, description }) {
  const details = sectionDetails[path]
  const Icon = details.icon

  return (
    <div className="section-page">
      <PageIntro eyebrow={details.eyebrow} title={title} description={description} />
      <Panel className="section-placeholder">
        <div className="section-placeholder__icon"><Icon size={22} strokeWidth={1.6} /></div>
        <p className="eyebrow">M10 FOUNDATION</p>
        <h2>{title} workspace</h2>
        <p className="section-placeholder__copy">{details.limitation}</p>
        <EmptyState
          title="No page data is connected yet"
          detail="This is an honest foundation state; no sample financial data or placeholder activity is shown."
          action={<Link className="text-link" to="/">Return to overview <span aria-hidden="true">→</span></Link>}
        />
      </Panel>
    </div>
  )
}

export default SectionPage
