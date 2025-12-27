import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.SUPABASE_URL || 'https://demo.supabase.co';
const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || 'demo-anon-key';

// Crear cliente con valores demo si no hay variables de entorno
export const supabase = createClient(supabaseUrl, supabaseAnonKey);