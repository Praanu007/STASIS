import { createRoot } from 'react-dom/client';
import { App } from './App';
import { boot } from './controller';

createRoot(document.getElementById('root') as HTMLElement).render(<App />);
void boot();
