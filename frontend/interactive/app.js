// Replace with your actual CDP URL (browser endpoint)
const CDP_URL = "ws://127.0.0.1:9222/devtools/browser/be1a84f0-f6b9-4a5b-ab03-fbe1c91ccbda";

// Connect to the WebSocket
const socket = new WebSocket(CDP_URL);

let sessionId = null;
let currentMetadata = null; // Holds metadata from screencastFrame for coordinate mapping

socket.addEventListener("open", () => {
  console.log("Connected to CDP server");
  document.getElementById("output").innerText = "Connected to CDP server";

  // Step 1: Get the list of targets
  const getTargets = {
    id: 1,
    method: "Target.getTargets",
    params: {}
  };
  socket.send(JSON.stringify(getTargets));
});

socket.addEventListener("message", (event) => {
  const data = JSON.parse(event.data);
  console.log("Received Data:", data);

  // Handle getTargets response
  if (data.id === 1 && data.result) {
    const pageTarget = data.result.targetInfos.find(t => t.type === "page");
    if (pageTarget) {
      // Attach to the page target
      const attachToTarget = {
        id: 2,
        method: "Target.attachToTarget",
        params: { targetId: pageTarget.targetId, flatten: true }
      };
      socket.send(JSON.stringify(attachToTarget));
    } else {
      console.error("No page target found!");
      document.getElementById("output").innerText = "No page target found!";
    }
  }

  // Handle attachToTarget response
  if (data.id === 2 && data.result) {
    sessionId = data.result.sessionId;

    // Enable the Page domain so we can start screencasting
    const pageEnable = {
      id: 3,
      method: "Page.enable",
      sessionId: sessionId,
      params: {}
    };
    socket.send(JSON.stringify(pageEnable));
  }

  // Handle Page.enable response
  if (data.id === 3 && !data.error) {
    console.log("Page domain enabled");
    // Bring the page to front
    const bringToFront = {
      id: 6,
      method: "Page.bringToFront",
      sessionId: sessionId
    };
    socket.send(JSON.stringify(bringToFront));

    // Start the screencast
    const startScreencast = {
      id: 4,
      method: "Page.startScreencast",
      sessionId: sessionId,
      params: {
        format: "png",
        quality: 80,
        everyNthFrame: 1
      }
    };
    socket.send(JSON.stringify(startScreencast));
  } else if (data.id === 3 && data.error) {
    console.error("Error enabling Page domain:", data.error);
  }

  // Handle screencast frames
  if (data.method === "Page.screencastFrame" && data.params) {
    const { data: imageData, metadata, sessionId: frameSessionId } = data.params;
    currentMetadata = metadata; // Store for coordinate translation

    // Update the <img> with the received frame
    const imgElement = document.getElementById("screencast");
    imgElement.src = `data:image/png;base64,${imageData}`;

    // Send ScreencastFrameAck to receive next frames
    const screencastFrameAck = {
      id: 5,
      method: "Page.screencastFrameAck",
      sessionId: sessionId,
      params: {
        sessionId: frameSessionId
      }
    };
    socket.send(JSON.stringify(screencastFrameAck));
  }
});

socket.addEventListener("error", (error) => {
  console.error("WebSocket Error:", error);
  document.getElementById("output").innerText = "Error connecting to CDP server.";
});

socket.addEventListener("close", () => {
  console.log("Connection closed");
  document.getElementById("output").innerText = "Connection closed.";
});


// === Input Event Handlers === //
const imgElement = document.getElementById('screencast');

// Mouse events
imgElement.addEventListener('mousedown', event => {
  if (sessionId && currentMetadata) {
    sendMouseEvent('mousePressed', event);
  }
});

imgElement.addEventListener('mouseup', event => {
  if (sessionId && currentMetadata) {
    sendMouseEvent('mouseReleased', event);
  }
});

imgElement.addEventListener('mousemove', event => {
  if (sessionId && currentMetadata) {
    sendMouseEvent('mouseMoved', event);
  }
});

// Keyboard events
document.addEventListener('keydown', event => {
  if (sessionId) {
    // Prevent default local behavior (e.g. page scrolling on space)
    event.preventDefault();
    // Send keyDown without text
    sendKeyEvent('keyDown', event, false);
    // If it's a character, send a char event
    if (event.key.length === 1) {
      sendKeyEvent('char', event, true);
    }
  }
});

document.addEventListener('keyup', event => {
  if (sessionId) {
    event.preventDefault();
    sendKeyEvent('keyUp', event, false);
  }
});

function sendMouseEvent(type, event) {
  const { deviceWidth, deviceHeight } = currentMetadata;
  const imgRect = imgElement.getBoundingClientRect();
  const imgWidth = imgRect.width;
  const imgHeight = imgRect.height;

  // Translate local image coordinates to browser coordinates
  const browserX = (event.offsetX / imgWidth) * deviceWidth;
  const browserY = (event.offsetY / imgHeight) * deviceHeight;

  const mouseParams = {
    id: 100,
    sessionId: sessionId,
    method: "Input.dispatchMouseEvent",
    params: {
      type: type,
      x: browserX,
      y: browserY,
      button: (type === 'mousePressed' || type === 'mouseReleased') ? "left" : "none",
      clickCount: 1
    }
  };
  socket.send(JSON.stringify(mouseParams));
}

function sendKeyEvent(type, event, isChar) {
  const keyParams = {
    id: 101,
    sessionId: sessionId,
    method: "Input.dispatchKeyEvent",
    params: {
      type: type,
      key: event.key,
      code: event.code,
      windowsVirtualKeyCode: event.keyCode,
      nativeVirtualKeyCode: event.keyCode,
      // Only add text for the char event
      text: isChar ? event.key : undefined
    }
  };
  
  socket.send(JSON.stringify(keyParams));
}

/* END CODE */
