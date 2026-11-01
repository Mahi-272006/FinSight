import { ArrowUpRight, RefreshCw, TriangleAlert } from 'lucide-react'
import { Link } from 'react-router-dom'

export function Button({ children, variant = 'primary', className = '', ...props }) {
  return (
    <button className={`button button--${variant} ${className}`.trim()} {...props}>
      {children}
    </button>
  )
}

export function LinkButton({ children, to, variant = 'secondary', className = '' }) {
  return (
    <Link className={`button button--${variant} ${className}`.trim()} to={to}>
      {children}
      <ArrowUpRight aria-hidden="true" size={15} />
    </Link>
  )
}

export function Panel({ title, eyebrow, action, children, className = '' }) {
  return (
    <section className={`panel ${className}`.trim()}>
      {(title || eyebrow || action) && (
        <div className="panel__header">
          <div>
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            {title && <h2 className="panel__title">{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function LoadingState({ label = 'Loading records' }) {
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="loading-mark" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, detail, action }) {
  return (
    <div className="state state--empty">
      <span className="state__mark" aria-hidden="true">—</span>
      <div>
        <p className="state__title">{title}</p>
        {detail && <p className="state__detail">{detail}</p>}
        {action && <div className="state__action">{action}</div>}
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="state state--error" role="alert">
      <TriangleAlert size={17} aria-hidden="true" />
      <div>
        <p className="state__title">Couldn’t load this section</p>
        <p className="state__detail">{message}</p>
        {onRetry && (
          <button className="text-button" type="button" onClick={onRetry}>
            <RefreshCw size={13} aria-hidden="true" /> Try again
          </button>
        )}
      </div>
    </div>
  )
}

export function DataTable({ children, className = '' }) {
  return <div className={`table-scroll ${className}`.trim()}><table>{children}</table></div>
}

export function PageIntro({ eyebrow, title, description, action }) {
  return (
    <div className="page-intro">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="page-intro__description">{description}</p>}
      </div>
      {action}
    </div>
  )
}
