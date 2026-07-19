import { useState, useEffect, useRef, useCallback } from 'react'
import { apiFetch as fetch } from '../apiFetch'

export default function LiveMode({ onSubmit, isProcessing, currentResponse }) {
  const [isListening, setIsListening] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState(null)
  const [config, setConfig] = useState(null)
  
  const recognitionRef = useRef(null)
  const audioContextRef = useRef(null)
  const audioQueueRef = useRef([])
  const isPlayingRef = useRef(false)
  const mediaStreamRef = useRef(null)
  
  // Load STT/TTS configuration
  useEffect(() => {
    fetch('/api/live/config')
      .then(res => res.json())
      .then(setConfig)
      .catch(err => setError('Failed to load voice configuration'))
  }, [])
  
  // Initialize Web Speech API for STT
  useEffect(() => {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
      setError('Speech recognition not supported in this browser. Please use Chrome or Edge.')
      return
    }
    
    // Check if we're in a secure context
    if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
      setError('Speech recognition requires HTTPS. Please access the app via https://')
      return
    }
    
    // Check if the context is secure enough for speech recognition
    if (!window.isSecureContext) {
      setError('Speech recognition requires a secure context. Please use a trusted certificate or access via localhost.')
      return
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    recognitionRef.current = new SpeechRecognition()
    recognitionRef.current.continuous = true
    recognitionRef.current.interimResults = true
    recognitionRef.current.lang = 'en-US'
    
    recognitionRef.current.onresult = (event) => {
      let interimTranscript = ''
      let finalTranscript = ''
      
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript
        if (event.results[i].isFinal) {
          finalTranscript += transcript
        } else {
          interimTranscript += transcript
        }
      }
      
      if (finalTranscript) {
        setTranscript(finalTranscript)
        // Auto-submit when we have a final result
        handleSubmit(finalTranscript)
      }
    }
    
    recognitionRef.current.onerror = (event) => {
      console.error('Speech recognition error:', event.error)
      
      let errorMessage = 'Speech recognition error: '
      switch (event.error) {
        case 'not-allowed':
        case 'permission-denied':
          errorMessage = 'Browser blocked speech recognition. This usually happens with self-signed certificates.\n\nSolutions:\n1. Use http://localhost:8081 (not https)\n2. Or install mkcert for trusted certificates\n3. Or use a production domain with Let\'s Encrypt'
          break
        case 'no-speech':
          errorMessage = 'No speech detected. Please try again.'
          break
        case 'audio-capture':
          errorMessage = 'No microphone found. Please connect a microphone.'
          break
        case 'network':
          errorMessage = 'Network error occurred. Please check your connection.'
          break
        case 'aborted':
          errorMessage = 'Speech recognition was aborted. Please try again.'
          break
        case 'service-not-allowed':
          errorMessage = 'Speech recognition service not allowed. Please use a trusted certificate or localhost.'
          break
        default:
          errorMessage += event.error
      }
      
      setError(errorMessage)
      setIsListening(false)
    }
    
    recognitionRef.current.onend = () => {
      setIsListening(false)
    }
    
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop()
      }
    }
  }, [])
  
  // Play TTS audio with streaming
  const playAudio = useCallback(async (text) => {
    if (!text || isSpeaking) return
    
    setIsSpeaking(true)
    setError(null)
    
    try {
      const response = await fetch('/api/live/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      
      if (!response.ok) {
        throw new Error(`TTS API error: ${response.status}`)
      }
      
      // Create audio context if needed
      if (!audioContextRef.current) {
        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
      }
      
      const audioContext = audioContextRef.current
      
      // Read the streaming response
      const reader = response.body.getReader()
      const chunks = []
      
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        chunks.push(value)
      }
      
      // Combine all chunks into a single buffer
      const audioData = new Uint8Array(chunks.reduce((acc, chunk) => acc + chunk.length, 0))
      let offset = 0
      for (const chunk of chunks) {
        audioData.set(chunk, offset)
        offset += chunk.length
      }
      
      // Decode and play
      const audioBuffer = await audioContext.decodeAudioData(audioData.buffer)
      const source = audioContext.createBufferSource()
      source.buffer = audioBuffer
      source.connect(audioContext.destination)
      
      source.onended = () => {
        setIsSpeaking(false)
        // Resume listening after speaking
        if (recognitionRef.current && !isListening) {
          startListening()
        }
      }
      
      source.start(0)
      
    } catch (err) {
      console.error('TTS error:', err)
      setError(`Failed to synthesize speech: ${err.message}`)
      setIsSpeaking(false)
    }
  }, [isSpeaking, isListening])
  
  // Speak the response when it arrives
  useEffect(() => {
    if (currentResponse && !isSpeaking && !isProcessing) {
      playAudio(currentResponse)
    }
  }, [currentResponse, isProcessing, playAudio, isSpeaking])
  
  const startListening = useCallback(() => {
    if (!recognitionRef.current || isListening) return
    
    setError(null)
    setTranscript('')
    
    try {
      recognitionRef.current.start()
      setIsListening(true)
    } catch (err) {
      console.error('Failed to start recognition:', err)
      setError('Failed to start speech recognition')
    }
  }, [isListening])
  
  const stopListening = useCallback(() => {
    if (!recognitionRef.current) return
    
    recognitionRef.current.stop()
    setIsListening(false)
  }, [])
  
  const handleSubmit = useCallback((text) => {
    if (!text || isProcessing) return
    
    stopListening()
    onSubmit(text)
  }, [isProcessing, onSubmit, stopListening])
  
  const toggleListening = useCallback(() => {
    if (isListening) {
      stopListening()
    } else {
      startListening()
    }
  }, [isListening, startListening, stopListening])
  
  if (error) {
    return (
      <div className="live-mode-error">
        <div className="error-icon">⚠️</div>
        <div className="error-message">{error}</div>
        <button onClick={() => setError(null)} className="retry-button">
          Retry
        </button>
      </div>
    )
  }
  
  return (
    <div className="live-mode">
      <div className="live-mode-status">
        {isProcessing && <div className="status processing">Processing...</div>}
        {isSpeaking && <div className="status speaking">Speaking...</div>}
        {isListening && <div className="status listening">Listening...</div>}
        {!isProcessing && !isSpeaking && !isListening && (
          <div className="status idle">Ready</div>
        )}
      </div>
      
      {transcript && (
        <div className="transcript-preview">
          <strong>You said:</strong> {transcript}
        </div>
      )}
      
      <button
        className={`live-mode-button ${isListening ? 'active' : ''} ${isSpeaking ? 'disabled' : ''}`}
        onClick={toggleListening}
        disabled={isSpeaking || isProcessing}
      >
        <div className="button-icon">
          {isListening ? '🎤' : '🔇'}
        </div>
        <div className="button-label">
          {isListening ? 'Listening...' : 'Start Voice Chat'}
        </div>
        {isListening && <div className="pulse-ring" />}
      </button>
      
      <div className="live-mode-hint">
        {isListening 
          ? 'Speak naturally. I\'ll respond automatically.'
          : 'Click to start hands-free voice conversation'
        }
      </div>
    </div>
  )
}
