// Replace with your actual CDP URL (browser endpoint)
const CDP_URL = "ws://127.0.0.1:9222/devtools/browser/60d164d2-10ae-45ea-af62-d9ca98ad5657";

// Connect to the WebSocket
const socket = new WebSocket(CDP_URL);

let sessionId = null;

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
    // Now start the screencast
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
    const { data: imageData, sessionId: frameSessionId } = data.params;

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