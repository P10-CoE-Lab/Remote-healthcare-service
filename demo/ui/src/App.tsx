import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ClientView } from './views/ClientView';
import { OperatorView } from './views/OperatorView';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

function AppRouter() {
  const isOperator = window.location.pathname === '/operator' ||
                     window.location.pathname.startsWith('/operator/');

  return isOperator ? <OperatorView /> : <ClientView />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AppRouter />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
