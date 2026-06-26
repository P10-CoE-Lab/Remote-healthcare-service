import { Component, type ReactNode } from 'react';
import { RefreshCw, AlertTriangle } from 'lucide-react';

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  reset = () => {
    this.setState({ error: null });
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-10 max-w-md w-full text-center">
            <div className="w-14 h-14 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4">
              <AlertTriangle size={26} className="text-red-500" />
            </div>
            <h2 className="text-base font-bold text-slate-900 mb-2">
              Something went wrong
            </h2>
            <p className="text-sm text-slate-500 mb-1">
              {this.state.error.message}
            </p>
            <p className="text-xs text-slate-400 mb-6">
              This is usually caused by the API restarting. Refreshing will reconnect.
            </p>
            <button
              onClick={this.reset}
              className="flex items-center gap-2 mx-auto px-5 py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-xl hover:bg-blue-700 transition-colors"
            >
              <RefreshCw size={15} />
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
