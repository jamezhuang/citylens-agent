export const demoPrompts: string[]
export function demoChat(payload: any, signal?: AbortSignal): Promise<any>
export function demoApi(path: string, method?: string, body?: any): Promise<any>
export function demoSnapshot(): {stage:number;writes:number;orderCount:number}
export function subscribeDemo(fn: (value: ReturnType<typeof demoSnapshot>) => void): () => void
export function resetDemo(): void
